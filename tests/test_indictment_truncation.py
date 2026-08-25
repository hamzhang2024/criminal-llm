"""起诉意见书截断检测：finish_reason=length 时自动续写，仍截断则标可见警告

背景（2026-08-24 王振栋案）：qwen 时代提取的起诉意见书在第一个小标题处被截断，
本案被告人的指控事实整段缺失，且无任何标记静默落库——后续分析全部建立在
残缺材料上。截断必须被检测并处理。
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import llm_client
from llm_client import LLMClient


def _make_client(responses):
    """构造走假 transport 的 LLMClient：responses = [(content, finish_reason), ...]"""
    client = LLMClient.__new__(LLMClient)
    client.purpose = "evidence"
    client.base_url = "http://x/v1"
    client.api_key = "k"
    client.model = "test-model"
    client.context_limit = 250000
    client._cache_hit_tokens = 0
    client._cache_miss_tokens = 0
    client._total_requests = 0
    client.timeout = __import__("httpx").Timeout(30)

    calls = []

    class FakeResponse:
        def __init__(self, content, finish):
            self._c, self._f = content, finish

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "choices": [{"message": {"content": self._c}, "finish_reason": self._f}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5},
            }

    class FakeAsyncClient:
        async def post(self, url, json=None, headers=None):
            calls.append(json)
            content, finish = responses[min(len(calls) - 1, len(responses) - 1)]
            return FakeResponse(content, finish)

    client.client = FakeAsyncClient()
    client._calls = calls
    # chat() 每次调用前 reload_config()，读真实配置且读超时不符会重建真实 httpx client
    # （测试环境会真发 HTTP → ConnectError），测试桩掉
    client.reload_config = lambda: None
    return client


def test_chat_records_finish_reason_stop():
    """正常结束：last_finish_reason = stop"""
    client = _make_client([("完整输出", "stop")])
    out = asyncio.run(client.chat([{"role": "user", "content": "x"}]))
    assert out == "完整输出"
    assert client.last_finish_reason == "stop"


def test_chat_records_finish_reason_length():
    """截断：last_finish_reason = length（调用方可检测）"""
    client = _make_client([("半截输出", "length")])
    asyncio.run(client.chat([{"role": "user", "content": "x"}]))
    assert client.last_finish_reason == "length"


def test_indictment_truncation_triggers_continuation(tmp_path, monkeypatch):
    """起诉意见书首次截断 → 自动续写一次并拼接"""
    import case_manager

    half = "### 总体指控\n- **指控罪名**：强奸罪\n\n### 逐笔犯罪事实\n#### 第1笔事实\n- **时间**：2015年"
    rest = "- **地点**：江阴市\n- **涉案人员及角色**：王某\n\n### 涉案人员汇总\n王某 | 123 | 嫌疑人"

    class FakeClient:
        def __init__(self):
            self.n = 0
            self.last_finish_reason = ""

        async def chat(self, messages, **kw):
            self.n += 1
            if self.n == 1:
                self.last_finish_reason = "length"
                return half
            if self.n == 2:
                self.last_finish_reason = "stop"
                return rest
            self.last_finish_reason = "stop"
            return "首行：起诉意见书\n末行：特此报告"

    fake = FakeClient()
    monkeypatch.setattr(llm_client, "_clients", {"evidence": fake})

    md = tmp_path / "第1卷_去水印.md"
    md.write_text("# 起诉意见书\n\n被告人王某涉嫌强奸罪。\n特此报告", encoding="utf-8")
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir()

    path = asyncio.run(case_manager._process_indictment_single(md, md.read_text(encoding="utf-8"), ev_dir, 1))
    content = path.read_text(encoding="utf-8")

    assert fake.n >= 2, "截断后未发起续写"
    # 拼接结果包含截断处之后的内容
    assert "江阴市" in content or "涉案人员汇总" in content


def test_indictment_still_truncated_marks_warning(tmp_path, monkeypatch):
    """续写后仍截断 → 证据带可见警告标记（不静默）"""
    import case_manager

    class FakeClient:
        def __init__(self):
            self.n = 0
            self.last_finish_reason = "length"  # 永远截断

        async def chat(self, messages, **kw):
            self.n += 1
            return "半截内容"

    fake = FakeClient()
    monkeypatch.setattr(llm_client, "_clients", {"evidence": fake})

    md = tmp_path / "第1卷_去水印.md"
    md.write_text("# 起诉意见书\n\n内容", encoding="utf-8")
    ev_dir = tmp_path / "evidence"
    ev_dir.mkdir()

    path = asyncio.run(case_manager._process_indictment_single(md, md.read_text(encoding="utf-8"), ev_dir, 1))
    content = path.read_text(encoding="utf-8")
    assert "截断" in content, "仍截断时缺少可见警告标记"


# ── A：嵌在卷里的起诉书切片走专用逐笔通道 ──

def test_embedded_indictment_routed_to_special_channel(tmp_path, monkeypatch):
    """程序性卷（含起诉意见书正文段）：起诉书切片走专用 prompt（逐笔），
    通用提取输入中剔除该段，专用块排在证据最前"""
    import llm_client as lc
    import case_manager

    indictment_body = "起诉意见书\n\n" + "被告人王某涉嫌强奸罪的详细指控事实。" * 60
    notice_body = "\n\n# 犯罪嫌疑人诉讼权利义务告知书\n\n（告知书正文内容）" * 10
    md_text = "# 卷内文书目录\n\n若干条目\n\n# " + indictment_body + notice_body

    calls = []

    class FakeClient:
        context_limit = 250000
        model = "test"
        last_finish_reason = "stop"

        async def chat(self, messages, **kw):
            content = messages[-1]["content"] if messages else ""
            calls.append(content)
            if "逐笔记录该文书指控的全部犯罪事实" in str(messages[0].get("content", "")):
                return "### 总体指控\n- **指控罪名**：强奸罪\n\n### 逐笔犯罪事实\n#### 第1笔\n- **时间**：2015年\n- **地点**：江阴市"
            return json.dumps([{"name": "诉讼权利义务告知书", "type": "程序性文书",
                                "summary": "告知内容", "original_quotes": ""}], ensure_ascii=False)

    monkeypatch.setattr(lc, "_clients", {"evidence": FakeClient()})
    md_file = tmp_path / "第1卷_去水印.md"
    md_file.write_text(md_text, encoding="utf-8")

    (tmp_path / "temp").mkdir()
    _, blocks = asyncio.run(case_manager._extract_single_file(
        md_file, md_text, tmp_path / "temp", ["强奸罪"]))

    assert blocks, "应有证据块"
    # 专用块在最前，且是起诉意见书类型
    assert blocks[0]["type"] == "起诉意见书"
    assert "逐笔犯罪事实" in blocks[0]["summary_preview"]
    # 通用提取的输入里不含起诉书正文段（专用通道的 user 消息用格式模板标记区分）
    generic_inputs = [c for c in calls if "请按以下格式输出完整分析" not in c]
    assert all("详细指控事实" not in c for c in generic_inputs), "通用路径仍收到起诉书正文段"


def test_slice_indictment_section():
    """切片定位：起诉意见书标题到下一个同级标题之间"""
    from case_manager import _slice_indictment_section
    text = "# 目录\n\n# 起诉意见书\n\n指控正文\n\n# 告知书\n\n告知正文"
    section = _slice_indictment_section(text)
    assert section is not None
    assert "指控正文" in section
    assert "告知正文" not in section
    assert _slice_indictment_section("# 讯问笔录\n\n问答") is None
