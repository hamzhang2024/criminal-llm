"""多罪名：5B 共享层去重 + stage_4 罪名层读取"""
import asyncio

from analysis_engine import AnalysisEngine


def test_5b_skipped_when_output_exists(tmp_path, monkeypatch):
    """5B 产物已存在且非空时跳过重跑（多罪名第二个罪名不再重复矛盾分析）"""
    case_path = tmp_path / "case"
    ad = case_path / "analysis"
    (ad / "stage_52").mkdir(parents=True)
    (ad / "stage_52" / "output.md").write_text("已有矛盾分析", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)

    called = []

    async def fake_5b(defendant, progress_cb=None):
        called.append(True)
        return "新矛盾分析"

    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    result = asyncio.run(engine._run_5b_if_needed("张三"))
    assert called == []  # 未重跑
    assert result == "已有矛盾分析"


def test_5b_runs_when_missing(tmp_path, monkeypatch):
    """5B 产物不存在时正常执行"""
    case_path = tmp_path / "case"
    (case_path / "analysis").mkdir(parents=True)
    engine = AnalysisEngine("c", case_path)

    called = []

    async def fake_5b(defendant, progress_cb=None):
        called.append(True)
        return "新矛盾分析"

    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    result = asyncio.run(engine._run_5b_if_needed("张三"))
    assert called == [True]
    assert result == "新矛盾分析"


def test_stage4_md_reads_charge_layer(tmp_path):
    """多罪名：stage4_md 读取罪名层产物 analysis/{charge}/stage_4/output.md"""
    case_path = tmp_path / "case"
    charge_dir = case_path / "analysis" / "诈骗罪" / "stage_4"
    charge_dir.mkdir(parents=True)
    (charge_dir / "output.md").write_text("诈骗罪法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    md = engine._read_stage4_for_charge("诈骗罪")
    assert md == "诈骗罪法规"


def test_stage4_md_shared_when_no_charge(tmp_path):
    """单罪名/共享层：读 analysis/stage_4/output.md"""
    case_path = tmp_path / "case"
    shared = case_path / "analysis" / "stage_4"
    shared.mkdir(parents=True)
    (shared / "output.md").write_text("共享法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    assert engine._read_stage4_for_charge(None) == "共享法规"


def test_stage4_md_falls_back_to_shared_when_charge_layer_missing(tmp_path):
    """传入罪名但罪名层文件缺失 → 回落共享层"""
    case_path = tmp_path / "case"
    shared = case_path / "analysis" / "stage_4"
    shared.mkdir(parents=True)
    (shared / "output.md").write_text("共享法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    assert engine._read_stage4_for_charge("诈骗罪") == "共享法规"


def test_stage4_md_falls_back_to_shared_when_charge_layer_empty(tmp_path):
    """传入罪名但罪名层文件为空 → 回落共享层，不被空串遮蔽"""
    case_path = tmp_path / "case"
    charge_dir = case_path / "analysis" / "诈骗罪" / "stage_4"
    charge_dir.mkdir(parents=True)
    (charge_dir / "output.md").write_text("", encoding="utf-8")
    shared = case_path / "analysis" / "stage_4"
    shared.mkdir(parents=True)
    (shared / "output.md").write_text("共享法规", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)
    assert engine._read_stage4_for_charge("诈骗罪") == "共享法规"


def test_5b_reruns_when_output_is_blank(tmp_path, monkeypatch):
    """stage_52 产物为纯空白（如 "  \\n"）→ 视为无产物，触发重跑"""
    case_path = tmp_path / "case"
    ad = case_path / "analysis"
    (ad / "stage_52").mkdir(parents=True)
    (ad / "stage_52" / "output.md").write_text("  \n", encoding="utf-8")
    engine = AnalysisEngine("c", case_path)

    called = []

    async def fake_5b(defendant, progress_cb=None):
        called.append(True)
        return "新矛盾分析"

    monkeypatch.setattr(engine, "stage_5b_contradiction_analysis", fake_5b)

    result = asyncio.run(engine._run_5b_if_needed("张三"))
    assert called == [True]
    assert result == "新矛盾分析"
