"""预算接入：各处截断点统一走 context_budget"""
import inspect
import analysis_engine
import analysis_pipeline
import case_manager
import llm_client


def test_analysis_engine_delegates_to_context_budget():
    src = inspect.getsource(analysis_engine._get_content_budget_chars)
    assert "context_budget" in src


def test_case_manager_uses_context_budget():
    src = inspect.getsource(case_manager._extract_single_file)
    assert "context_budget" in src
    assert "38000" not in src  # 旧公式移除


def test_pipeline_no_hardcoded_large_slices():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step3_internal_contradiction)
    assert "[:40000]" not in src
    src4 = inspect.getsource(analysis_pipeline.AnalysisPipeline.step4_build_case_wiki)
    assert "[:30000]" not in src4
    assert "[:15000]" not in src4


def test_llm_client_report_slice_budget_aware():
    src = inspect.getsource(llm_client)
    assert "report_context[:50000]" not in src
    assert "original_report[:60000]" not in src


def test_llm_client_evidence_slice_budget_aware():
    src = inspect.getsource(llm_client)
    assert "evidence_context[:40000]" not in src


def test_pipeline_step2_no_hardcoded_slice():
    src = inspect.getsource(analysis_pipeline.AnalysisPipeline.step2_detailed_summaries)
    assert "[:30000]" not in src
