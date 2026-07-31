"""改动 F：矛盾分析边界——步骤 3 只做供述内矛盾，4b 只管证据间矛盾"""
import inspect
import analysis_pipeline


def test_step3_prompt_declares_internal_contradiction_only():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step3_internal_contradiction)
    assert "供述内矛盾" in src
    assert "不分析不同证据之间的矛盾" in src


def test_step4b_prompt_declares_inter_evidence_contradiction():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step4_build_case_wiki)
    assert "证据间矛盾" in src
    assert "同一人口供前后矛盾已在步骤 3 完成" in src
