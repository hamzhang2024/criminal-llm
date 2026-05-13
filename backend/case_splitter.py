"""
刑事案卷智能拆分模块 v3.0

核心逻辑（简化）：
1. 检测文书边界（关键词匹配）
2. 每个拆分文件只看第一页判断类型
   - 有文字层 → 关键词匹配
   - 纯图片 → 多模态 LLM 判断
3. 简化命名：人名+文书类型

参考：criminal-pdf-splitter 技能
"""
import json
import re
import base64
from pathlib import Path
from typing import Dict, List, Any, Optional, Tuple, Callable
from dataclasses import dataclass, field

# 配置文件路径
CONFIG_PATH = Path(__file__).parent.parent / "config" / "doc_types.json"


@dataclass
class DocumentSegment:
    """文书片段（连续页面）"""
    start_page: int
    end_page: int
    doc_type: str
    suspect_name: str = ""


# ============================================================
# 第一页文字判断 — 文书类型映射
# ============================================================
# 匹配到的关键词 → (最终文书类型, 人名提取模式)
# 设计原则：简单直接，看到什么映射什么

DOC_TYPE_RULES = [
    # 笔录类（核心）
    ("讯问笔录", r"讯问笔录", r"犯罪嫌疑人[：:]\s*([^\s，,。\.、\n]{2,4})"),
    ("讯问笔录", r"讯问[权利义务]*告知书", r"犯罪嫌疑人[：:]\s*([^\s，,。\.、\n]{2,4})"),
    ("询问笔录", r"询问笔录", r"被询问人[：:]\s*([^\s，,。\.、\n]{2,4})"),
    ("询问笔录", r"询问[权利义务]*告知书", r"被询问人[：:]\s*([^\s，,。\.、\n]{2,4})"),
    ("辨认笔录", r"辨认笔录", r"辨认人[：:]\s*([^\s，,。\.、\n]{2,4})"),

    # 程序性文书
    ("拘留证", r"拘留[证通知书]", ""),
    ("逮捕证", r"逮捕[证通知书]", ""),
    ("取保候审决定书", r"取保候审", ""),
    ("监视居住决定书", r"监视居住", ""),
    ("释放通知书", r"释放[通知书证明]", ""),
    ("传唤/传讯", r"传[讯唤][通知书]", ""),

    # 立案/受案
    ("受案登记表", r"受案登记", ""),
    ("立案决定书", r"立案决定", ""),

    # 侦查文书
    ("搜查证", r"搜查[证笔录]", ""),
    ("扣押清单", r"扣押[清单决定]", ""),
    ("勘验笔录", r"勘验.*笔录", ""),
    ("检查笔录", r"检查笔录", ""),
    ("鉴定意见", r"鉴定[意见报告书]", ""),
    ("侦破经过", r"[侦破抓获到案破案].*经过", ""),

    # 结案文书
    ("起诉意见书", r"起诉意见书", ""),
    ("移送起诉告知书", r"移送起诉", ""),

    # 其他材料
    ("户籍证明", r"户籍证明", r"姓\s*名[：:]\s*([^\s，,。\.、\n]{2,4})"),
    ("前科材料", r"[刑事判决书前科]", ""),
    ("犯罪嫌疑人照片", r"犯罪嫌疑人照片", ""),
    ("接受证据材料", r"接受证据", ""),
    ("卷宗封面", r"卷宗", ""),
    ("卷内目录", r"卷内目录|文书目录", ""),
    ("卷宗封底", r"卷宗封底", ""),
]

# 不触发边界检测的词（出现在页面中间不代表新文书）
NON_BOUNDARY_WORDS = [
    "讯问笔录", "询问笔录", "辨认笔录",
]


class CriminalCaseSplitter:
    """刑事案卷智能拆分器 v3.0"""

    def __init__(self):
        self.config = self._load_config()

    def _load_config(self) -> Dict:
        """加载配置文件"""
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        return {}

    # ============================================================
    # 边界检测（关键词匹配前 300 字符）
    # ============================================================

    def detect_document_boundaries(self, page_texts: Dict[int, str], total_pages: int) -> List[int]:
        """
        检测文书边界 — 每份新文书的起始页
        """
        boundaries = [1]  # 第一页总是边界

        for page_num in range(2, total_pages + 1):
            text = page_texts.get(page_num, "")
            if not text:
                continue

            # 检查前 300 字符
            first_part = text[:300]

            # 检查边界关键词（所有文书类型标题都可能标志着新文书）
            all_keywords = [kw for _, kw, _ in DOC_TYPE_RULES]
            for keyword in all_keywords:
                if re.search(keyword, first_part):
                    boundaries.append(page_num)
                    break

            # 额外检测：编号标题模式
            lines = text.split('\n')[:3]
            for line in lines:
                line = line.strip()
                if re.match(r'^[一二三四五六七八九十]+[、．]', line):
                    boundaries.append(page_num)
                    break
                if re.match(r'^[（(][一二三四五六七八九十0-9]+[)）]', line):
                    boundaries.append(page_num)
                    break

        return sorted(set(boundaries))

    # ============================================================
    # 第一页文字判断文书类型
    # ============================================================

    def classify_first_page(self, first_page_text: str) -> Tuple[str, str]:
        """
        根据第一页文字判断文书类型

        Returns:
            (文书类型, 人名)
        """
        if not first_page_text:
            return "其他文书", ""

        text_to_check = first_page_text[:1500]

        for doc_type, keyword_pattern, name_pattern in DOC_TYPE_RULES:
            if re.search(keyword_pattern, text_to_check):
                # 提取人名
                name = ""
                if name_pattern:
                    name = self._extract_name(first_page_text, name_pattern)
                return doc_type, name

        return "其他文书", ""

    def _extract_name(self, text: str, pattern: str) -> str:
        """根据正则提取人名"""
        invalid_names = {
            "办案民警", "侦查人员", "询问人", "记录人", "民警", "警官",
            "犯罪嫌疑人", "被讯问人", "被询问人", "证人", "被害人",
            "辨认人", "勘查人", "检查人", "鉴定人",
        }

        match = re.search(pattern, text[:1500])
        if match:
            name = match.group(1).strip()
            if name and name not in invalid_names and len(name) >= 2:
                if not name.isdigit() and not re.search(r'[\d\-_]', name):
                    return name
        return ""

    # ============================================================
    # 多模态 LLM 判断（纯图片页 fallback）
    # ============================================================

    async def classify_with_vision(
        self,
        first_page_image_base64: str,
    ) -> Tuple[str, str]:
        """
        用多模态 LLM 判断纯图片页的文书类型

        Args:
            first_page_image_base64: 第一页图片的 base64 编码（JPEG, 72dpi）

        Returns:
            (文书类型, 人名) — 人名可能为空
        """
        try:
            from llm_client import LLMClient
            client = LLMClient()

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是一个刑事案卷文书识别助手。请查看这张图片，判断它是什么类型的文书。"
                        "只返回 JSON 格式，格式如下：\n"
                        '{"doc_type": "文书类型", "name": "人名（如有）"}\n\n'
                        "文书类型可选：讯问笔录、询问笔录、辨认笔录、拘留证、逮捕证、"
                        "取保候审决定书、搜查证、扣押清单、鉴定意见、起诉意见书、"
                        "户籍证明、卷宗封面、卷内目录、其他文书。\n"
                        "如果能从文书中提取到嫌疑人或证人姓名，填入 name 字段，否则留空字符串。"
                    )
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/jpeg;base64,{first_page_image_base64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "请识别这张刑事案卷第一页的文书类型。"
                        }
                    ]
                }
            ]

            response = await client.chat(messages)

            # 解析 JSON
            json_match = re.search(r'\{.*\}', response, re.DOTALL)
            if json_match:
                result = json.loads(json_match.group())
                return result.get("doc_type", "其他文书"), result.get("name", "")

            return "其他文书", ""

        except Exception as e:
            print(f"[拆分器] 多模态分类失败: {e}")
            return "其他文书", ""

    # ============================================================
    # 主入口：创建拆分方案
    # ============================================================

    def create_split_plan(
        self,
        page_texts: Dict[int, str],
        total_pages: int,
        source_filename: str = "",
        vision_classifier: Optional[Callable] = None,
    ) -> List[Dict]:
        """
        创建拆分方案

        Args:
            page_texts: 页面文本映射（页码 → 文字）
            total_pages: 总页数
            source_filename: 源文件名（不含 .pdf）
            vision_classifier: 可选，多模态分类器 async(page_image_base64) → (doc_type, name)

        Returns:
            拆分方案列表
        """
        # 1. 检测边界
        boundaries = self.detect_document_boundaries(page_texts, total_pages)

        # 2. 逐个判断类型
        segments: List[DocumentSegment] = []

        for i, start in enumerate(boundaries):
            end = boundaries[i + 1] - 1 if i + 1 < len(boundaries) else total_pages
            first_page_num = start
            first_text = page_texts.get(first_page_num, "")

            # 先尝试文字判断
            doc_type, name = self.classify_first_page(first_text)

            # 文字判断失败（文字很少或完全无文字）且有 vision_classifier
            if doc_type == "其他文书" and vision_classifier and len(first_text.strip()) < 20:
                # 标记需要视觉判断，延迟处理
                segments.append(DocumentSegment(
                    start_page=start, end_page=end,
                    doc_type="__need_vision__", suspect_name=""
                ))
                continue

            segments.append(DocumentSegment(
                start_page=start, end_page=end,
                doc_type=doc_type, suspect_name=name
            ))

        # 3. 合并相同类型的连续片段
        merged = self._merge_consecutive_segments(segments)

        # 4. 生成输出
        results = []
        for i, seg in enumerate(merged):
            # 命名：源文件名_序号_人名+文书类型
            desc = self._build_name(seg)
            name = f"{source_filename}_{i+1}_{desc}" if source_filename else f"{i+1}_{desc}"

            results.append({
                "id": f"split_{i}",
                "name": name,
                "start_page": seg.start_page,
                "end_page": seg.end_page,
                "doc_type": seg.doc_type,
                "suspect_name": seg.suspect_name,
                "pages": list(range(seg.start_page, seg.end_page + 1))
            })

        return results

    async def create_split_plan_with_vision(
        self,
        page_texts: Dict[int, str],
        total_pages: int,
        source_filename: str = "",
        pdf_path: Optional[str] = None,
    ) -> List[Dict]:
        """
        创建拆分方案（支持多模态 LLM 判断纯图片页）

        先做文字判断，对无法判断的页面提取图片做多模态判断。
        """
        # 第一步：文字判断
        results = self.create_split_plan(
            page_texts=page_texts,
            total_pages=total_pages,
            source_filename=source_filename,
        )

        # 第二步：检查是否有需要多模态判断的
        need_vision = [r for r in results if r.get("doc_type") == "__need_vision__" or
                       (r.get("doc_type") == "其他文书" and pdf_path)]

        if need_vision and pdf_path:
            try:
                import fitz
                doc = fitz.open(pdf_path)

                for result in results:
                    if result.get("doc_type") == "__need_vision__":
                        page_idx = result["start_page"] - 1
                        if 0 <= page_idx < len(doc):
                            # 渲染第2页为图片（优先第2页，更准）
                            page_idx_v = min(page_idx + 1, len(doc) - 1)
                            page = doc[page_idx_v]
                            pix = page.get_pixmap(dpi=72)
                            img_bytes = pix.tobytes("jpeg", jpg_quality=75)
                            img_base64 = base64.b64encode(img_bytes).decode("utf-8")

                            # 多模态判断
                            doc_type, name = await self.classify_with_vision(img_base64)
                            result["doc_type"] = doc_type
                            result["suspect_name"] = name
                            # 重新生成名字
                            seg = DocumentSegment(
                                start_page=result["start_page"],
                                end_page=result["end_page"],
                                doc_type=doc_type,
                                suspect_name=name
                            )
                            desc = self._build_name(seg)
                            src = source_filename
                            result["name"] = f"{src}_{result['id'].split('_')[1]}_{desc}" if src else f"{result['id'].split('_')[1]}_{desc}"

                doc.close()

                # 过滤掉仍然无法识别的
                results = [r for r in results if r.get("doc_type") != "__need_vision__"]

            except Exception as e:
                print(f"[拆分器] 多模态 fallback 失败: {e}")

        return results

    # ============================================================
    # 命名和合并
    # ============================================================

    def _build_name(self, seg: DocumentSegment) -> str:
        """生成文书描述名"""
        if seg.suspect_name:
            return f"{seg.suspect_name}{seg.doc_type}"
        return seg.doc_type

    def _merge_consecutive_segments(self, segments: List[DocumentSegment]) -> List[DocumentSegment]:
        """
        合并相同类型的连续片段

        规则：
        - 讯问/辨认：同一人连续 → 合并
        - 询问笔录：连续就合并（不管是否同一人）
        - 程序性文书分组：同组连续 → 合并
        - 其他：各自独立
        """
        if not segments:
            return []

        # 程序性文书分组
        procedural_groups = {
            "强制措施文书": {"拘留证", "逮捕证", "取保候审决定书", "监视居住决定书", "释放通知书", "传唤/传讯"},
            "立案材料": {"受案登记表", "立案决定书"},
        }

        def get_group(doc_type: str) -> Optional[str]:
            for group, types in procedural_groups.items():
                if doc_type in types:
                    return group
            return None

        merged = [segments[0]]

        for seg in segments[1:]:
            last = merged[-1]

            # 程序性文书同组合并
            seg_group = get_group(seg.doc_type)
            last_group = get_group(last.doc_type)
            if seg_group and last_group and seg_group == last_group:
                last.end_page = seg.end_page
                last.doc_type = seg_group
                continue

            # 讯问/辨认：同人合并
            if seg.doc_type in ("讯问笔录", "辨认笔录"):
                if (seg.doc_type == last.doc_type and
                    seg.suspect_name and
                    seg.suspect_name == last.suspect_name):
                    last.end_page = seg.end_page
                else:
                    merged.append(seg)

            # 询问笔录：连续就合并
            elif seg.doc_type == "询问笔录":
                if last.doc_type == "询问笔录":
                    last.end_page = seg.end_page
                    last.suspect_name = ""  # 不显示具体证人
                else:
                    merged.append(seg)

            # 其他不合并
            else:
                merged.append(seg)

        # 剔除封面/封底
        merged = [s for s in merged if s.doc_type not in ("卷宗封面", "卷宗封底", "卷内目录")]

        return merged

    def to_json(self, results: List[Dict]) -> List[Dict]:
        return results


def create_splitter() -> CriminalCaseSplitter:
    """创建拆分器实例"""
    return CriminalCaseSplitter()
