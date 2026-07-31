"""类案裁判规则检索：按罪名从案例库拉取 top N 卡片

定位：自动检索，供分析参考；报告正式引用仍需人工确认（可核验性底线）。
降级：无 API Key / 检索失败 / 0 结果 → 静默跳过（返回空），法条路径照常。
"""
import requests

from case_search_api import _service_config, fetch_case_cards, TIMEOUT

DISCLAIMER = "# 类案裁判规则（自动检索，供分析参考，正式引用需人工确认）\n\n"


def _format_rules_md(cards: list[dict]) -> str:
    md = DISCLAIMER
    for c in cards:
        md += f"## 【{c.get('case_no', '')}】{c.get('title', '')}\n\n"
        md += f"- 主要问题：{c.get('issue', '')}\n"
        md += f"- 裁判要旨：{c.get('holding_summary', '')}\n"
        md += f"- 裁判理由摘录：{c.get('reasoning_excerpt', '')}\n\n"
    return md


def fetch_case_rules(charges: list[str], size: int = 3) -> dict[str, str]:
    """按罪名检索类案卡片并格式化为 Markdown，返回 {罪名: md}。

    - 单罪名 HTTP 错误：跳过该罪名继续下一个
    - 连接级失败（云端不可达）：终止剩余罪名
    - 无 API Key / 0 结果：静默降级
    """
    base, key = _service_config()
    if not key:
        return {}
    rules: dict[str, str] = {}
    for charge in charges:
        try:
            resp = requests.get(
                f"{base}/api/cases/search",
                params={"charge": charge, "size": size},
                headers={"X-API-Key": key},
                timeout=TIMEOUT,
            )
        except Exception:
            # 连接级失败（含 requests 被替换/云端不可达）：终止剩余罪名
            break
        if resp.status_code != 200:
            continue
        case_nos = [r["case_no"] for r in resp.json().get("results", [])]
        if not case_nos:
            continue
        cards = fetch_case_cards(case_nos)
        if cards:
            rules[charge] = _format_rules_md(cards)
    return rules
