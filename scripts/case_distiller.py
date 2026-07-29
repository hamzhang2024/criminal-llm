"""案例卡片 LLM 提炼：百炼 OpenAI 兼容接口，严格 JSON 输出 + 校验 + 重试一次"""
import json

PROMPT_TEMPLATE = """你是法律知识工程师。请从以下《刑事审判参考》案例中提炼结构化信息。
只输出严格 JSON（不要输出任何其他内容），格式：
{{
  "charges": ["涉及罪名1", "罪名2"],
  "holding_summary": "裁判要旨，200-400字，概括本案确立的裁判规则，不得编造原文没有的内容",
  "keywords": ["5-10个检索关键词，含罪名、行为特征、法律概念"]
}}

案例标题：{title}

案例原文（节选）：
{excerpt}"""

EXCERPT_MAX = 6000


def validate_card(data: dict) -> list[str]:
    """校验卡片字段，返回错误列表（空列表 = 通过）"""
    errors = []
    charges = data.get("charges")
    if not isinstance(charges, list) or not charges or not all(isinstance(c, str) and c for c in charges):
        errors.append("charges 必须是非空字符串列表")
    summary = data.get("holding_summary")
    if not isinstance(summary, str) or not (100 <= len(summary) <= 600):
        errors.append("holding_summary 长度须在 100-600 字")
    keywords = data.get("keywords")
    if not isinstance(keywords, list) or not (3 <= len(keywords) <= 15):
        errors.append("keywords 须为 3-15 个元素的列表")
    return errors


def distill_case(session, base_url: str, api_key: str, model: str, title: str, md_text: str) -> dict:
    """调 LLM 提炼单篇案例。session 为 requests.Session（测试注入假 session）。失败重试一次后抛 RuntimeError"""
    prompt = PROMPT_TEMPLATE.format(title=title, excerpt=md_text[:EXCERPT_MAX])
    last_err: Exception | None = None
    for _attempt in range(2):
        try:
            resp = session.post(
                f"{base_url.rstrip('/')}/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                },
                timeout=300,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            data = json.loads(content)
            errors = validate_card(data)
            if errors:
                raise ValueError("卡片校验失败: " + "; ".join(errors))
            return data
        except Exception as e:
            last_err = e
    raise RuntimeError(f"提炼失败（已重试）: {last_err}")
