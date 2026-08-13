"""资金流梳理共享模块（pipeline 引擎 4e 与 stage 引擎 stage_35 共用）

职责：
- 资金类关键词抽取（从原始 MD 全文 + 证据摘要双源）
- 资金流梳理 prompt 构建（四档印证结论）

设计要点：
- 双源扫描：证据提取后的摘要（evidence/）可能漏掉流水细节，
  必须同时扫转换后的原始 MD 全文（md/），截图 OCR 文字也在其中
"""
import re
from typing import List, Dict

# 资金类内容关键词（覆盖流水/凭证/言词证据中的资金表述）
FUND_KEYWORDS_RE = re.compile(
    r"(转账|汇款|收款|付款|银行流水|交易明细|银行卡|卡号|账号|支付宝|"
    r"微信(?:支付|红包|转账)|借条|欠条|涉案金额|资金|现金|取现|万元|\d+元)"
)


def collect_fund_paragraphs(
    texts: List[Dict],
    max_chars: int,
    per_file_chars: int = 3000,
) -> str:
    """从文本列表中抽取资金相关段落，控制 token 预算

    逐份文本按空行分段，保留命中资金关键词的段落；单份上限 per_file_chars，
    总量超 max_chars 停止追加。无命中返回空串。

    Args:
        texts: [{"filename": str, "text": str}, ...]
        max_chars: 总量预算
        per_file_chars: 单份上限
    """
    parts = []
    total = 0
    for f in texts:
        paragraphs = [p for p in f.get("text", "").split("\n\n") if FUND_KEYWORDS_RE.search(p)]
        if not paragraphs:
            continue
        excerpt = "\n\n".join(paragraphs)[:per_file_chars]
        block = f"### {f.get('filename', '未知')}\n{excerpt}\n"
        if total + len(block) > max_chars:
            break
        parts.append(block)
        total += len(block)
    return "\n".join(parts)


def build_fund_prompt(indictment_content: str, fund_evidence: str) -> str:
    """构建资金流梳理 prompt

    有起诉书（指控要素）时做逐笔对照验证（四档结论 + 反向发现）；
    无起诉书时只做资金流重建。
    """
    has_indictment = bool(indictment_content) and not indictment_content.startswith(("本案未发现", "分析失败"))
    indictment_section = (
        f"## 指控要素（含涉案金额及计算方式）\n{indictment_content[:5000]}"
        if has_indictment else
        "## 指控要素\n本案未发现起诉书/起诉意见书，跳过对照验证，只做资金流重建"
    )
    return f"""{indictment_section}

## 证据中的资金相关内容
{fund_evidence}

请完成以下分析：
1. 【资金流重建】从证据中抽取全部资金往来记录，输出时间序表格：
   | 时间 | 付款方 | 收款方 | 金额 | 渠道 | 来源类型 | 证据出处 |
   来源类型必须标注：客观证据·银行流水 / 客观证据·转账凭证 / 言词证据·被告人供述 / 言词证据·被害人陈述 / 言词证据·证人证言 等
2. 【逐笔对照验证】（仅当提供了指控要素时执行）以起诉书指控的每笔涉案金额为基准逐笔核对，输出对照表：
   | 指控笔次 | 指控金额 | 指控时间 | 来源类型 | 证据出处 | 流水金额 | 核对结论 |
   核对结论分四档：
   - ✅客观证据印证：有流水/凭证直接支撑
   - 🗣仅言词证据：只有供述/证言提及、无客观证据印证（指出刑诉法第55条：重证据、不轻信口供，只有被告人供述不能定案）
   - ⚠️证据矛盾：言词与客观证据之间、或不同言词证据间金额/时间冲突（说明冲突点）
   - ❌无证据支撑：指控金额找不到任何来源
   另列 🔍反向发现：证据中有、起诉书未指控的大额资金往来
3. 【差异与疑点分析】
4. 【对辩护的意义】

请输出 Markdown 格式。"""


FUND_SYSTEM_PROMPT = "你是刑事律师，擅长经济犯罪案件的资金流分析。请基于证据材料梳理资金链条并验证指控金额。"
