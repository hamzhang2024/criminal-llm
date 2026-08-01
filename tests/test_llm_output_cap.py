from llm_client import DEFAULT_OUTPUT_CAP, compute_max_output_tokens


def test_deepseek_capped():
    assert compute_max_output_tokens(1000000, "deepseek-v4-pro") == 65536


def test_within_cap_uses_computed():
    # 60000 * 0.8 = 48000 < 65536，不应被截断
    assert compute_max_output_tokens(60000, "deepseek-v4-flash") == 48000


def test_kimi_family():
    assert compute_max_output_tokens(1000000, "kimi-k3") == 65536


def test_unknown_model_default_cap():
    assert compute_max_output_tokens(1000000, "some-future-model") == DEFAULT_OUTPUT_CAP


def test_small_context_unaffected():
    # 16000 * 0.8 = 12800，低于所有上限
    assert compute_max_output_tokens(16000, "deepseek-v4-pro") == 12800
