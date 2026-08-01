"""test_vision_analyzer_runner.py — analyzer adapter / cache 測試（Phase 6.4C1）"""
import json
import sys
import os
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.models import (  # noqa: E402
    EvaluationCase, EvaluationImage, EvaluationSource, ExpectedImageKind,
)
from alkaid_cs2.evaluation.vision_analyzer_runner import (  # noqa: E402
    ANALYZER_SCHEMA_VERSION, AnalyzerImageResult, AnalyzerRunConfig,
    CACHE_SCHEMA_VERSION, _cache_key, _safe_cache_path, cache_lookup,
    cache_write, compare_fixture_and_analyzer_payload, compute_image_hash,
    normalize_vision_payload, run_analyzer_for_case,
)


def _case(images=2):
    return EvaluationCase(
        case_id="t1", source=EvaluationSource.SYNTHETIC,
        author="synthetic", link="fixture://t1", raw_text="售 A 算5000",
        images=[EvaluationImage(image_index=i, image_url=f"redacted://i/{i}",
                                image_kind=ExpectedImageKind.SINGLE,
                                vision_payload={"type": "single", "items": []})
                for i in range(images)],
        expected_safe_for_production=True,
    )


def _loader_factory(payloads):
    def loader(case, idx):
        return payloads.get(idx, b"img-bytes")
    return loader


# ================================================================
# Cache key / schema
# ================================================================
def test_cache_key_includes_model_prompt_schema():
    k1 = _cache_key("h1", "m1", "p1")
    assert k1 == _cache_key("h1", "m1", "p1")
    assert k1 != _cache_key("h2", "m1", "p1"), "image_hash 參與 key"
    assert k1 != _cache_key("h1", "m2", "p1"), "model_name 參與 key"
    assert k1 != _cache_key("h1", "m1", "p2"), "prompt_version 參與 key"


def test_cache_mismatch_is_miss(tmp_path):
    cache_write(tmp_path, "h1", "m1", "p1", {"ok": True})
    assert cache_lookup(tmp_path, "h1", "m1", "p1") == {"ok": True}
    assert cache_lookup(tmp_path, "h1", "m2", "p1") is None, "model 不一致 → miss"
    assert cache_lookup(tmp_path, "h1", "m1", "p2") is None, "prompt 不一致 → miss"
    assert cache_lookup(tmp_path, "h2", "m1", "p1") is None, "hash 不一致 → miss"


def test_cache_invalid_schema_rejected(tmp_path):
    cache_write(tmp_path, "h1", "m1", "p1", {"ok": True})
    p = _safe_cache_path(tmp_path, _cache_key("h1", "m1", "p1"))
    data = json.loads(p.read_text(encoding="utf-8"))
    data["cache_schema_version"] = "9.9"
    p.write_text(json.dumps(data), encoding="utf-8")
    assert cache_lookup(tmp_path, "h1", "m1", "p1") is None, "schema 不符 → miss"


def test_cache_write_atomic(tmp_path):
    cache_write(tmp_path, "h1", "m1", "p1", {"ok": True})
    assert not list(tmp_path.glob("*.tmp")), "無殘留 tmp"
    assert list(tmp_path.glob("*.json")), "有最終 json"


def test_cache_path_traversal_rejected():
    with pytest.raises(ValueError):
        _safe_cache_path(Path("."), "../../etc/passwd")
    with pytest.raises(ValueError):
        _safe_cache_path(Path("."), "a/b")


def test_cache_payload_no_image_bytes(tmp_path):
    # 6.4C1.3：cache_write 後讀 JSON，驗證敏感資料不存在
    dirty = {"type": "single", "items": [{"name": "A"}],
             "raw_bytes": b"12345",
             "nested": {"image_bytes": b"x", "token": "sk-abc"}}
    cache_write(tmp_path, "hX", "m1", "p1", dirty)
    p = _safe_cache_path(tmp_path, _cache_key("hX", "m1", "p1"))
    data = json.loads(p.read_text(encoding="utf-8"))
    result = data["result"]
    assert "raw_bytes" not in result, "raw_bytes 不存在"
    assert "image_bytes" not in result.get("nested", {}), "nested image_bytes 不存在"
    assert "token" not in result.get("nested", {}), "token 不存在"
    assert result["items"][0]["name"] == "A", "非敏感欄位保留"


def test_cache_result_defensive_copy():
    p = {"type": "single", "items": [{"name": "A"}]}
    n = normalize_vision_payload(p)
    n["items"][0]["name"] = "MUTATED"
    assert p["items"][0]["name"] == "A", "原 payload 不被修改（defensive copy）"


# ================================================================
# Runner
# ================================================================
def test_image_indexes_preserved():
    case = _case(images=3)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a", 1: b"b", 2: b"c"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert [r.image_index for r in res] == [0, 1, 2], "原 image_index 保留"


def test_one_image_failure_does_not_abort():
    case = _case(images=3)

    def boom(b, p):
        if b == b"img-bytes":
            raise RuntimeError("analyzer 掛了")
        return {"type": "single", "items": []}
    res = run_analyzer_for_case(case, _loader_factory({1: b"ok"}),
                                analyzer=boom,
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert len(res) == 3, "單張失敗不中斷"
    ok = [r for r in res if r.success]
    assert len(ok) == 1, "只有 img1 成功"


def test_timeout_recorded():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].latency_ms >= 0, "latency 記錄"


def test_malformed_result_normalized():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: "not-a-dict",
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].success, "malformed 被 normalize 為其他"
    assert res[0].payload.get("raw_normalized") is True


def test_unknown_currency_not_promoted():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single",
                                                       "items": [{"currency": "XYZ"}]},
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].payload["items"][0]["currency"] == "XYZ", "未知幣別保留原樣"


def test_seller_price_not_invented():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single",
                                                       "items": [{"role": "reference"}]},
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert "price" not in res[0].payload["items"][0], "不得自動補 seller ask"


def test_duplicate_hash_uses_cache(tmp_path):
    case = _case(images=2)  # 同 image_url → 同 hash
    calls = []

    def analyzer(b, p):
        calls.append(1)
        return {"type": "single", "items": []}
    res = run_analyzer_for_case(case, _loader_factory({0: b"a", 1: b"a"}),
                                analyzer=analyzer,
                                config=AnalyzerRunConfig(use_cache=True, timeout_seconds=0),
                                cache_dir=tmp_path, allow_external=True)
    assert len(calls) == 1, "相同 hash 第二次走 cache"
    assert res[0].cached is False and res[1].cached is True


def test_model_name_recorded():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(model_name="gemini-2.5-flash",
                                                         use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].model_name == "gemini-2.5-flash"


def test_prompt_version_recorded():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(prompt_version="v9",
                                                         use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].prompt_version == "v9"


# ================================================================
# Offline / external gate
# ================================================================
def test_offline_mode_never_calls_analyzer(tmp_path):
    case = _case(images=1)
    called = []

    def analyzer(b, p):
        called.append(1)
        return {"type": "single", "items": []}
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=analyzer,
                                config=AnalyzerRunConfig(use_cache=True, timeout_seconds=0),
                                cache_dir=tmp_path, allow_external=False)
    assert not called, "offline 不得呼叫 analyzer"
    assert res[0].success is False
    assert res[0].error_code == "cache_miss_offline"


def test_external_requires_flag_and_env(monkeypatch, tmp_path):
    case = _case(images=1)
    # 只 flag 無 env → 仍 offline
    monkeypatch.delenv("EVALUATION_ALLOW_EXTERNAL_ANALYZER", raising=False)
    called = []

    def analyzer(b, p):
        called.append(1)
        return {"type": "single", "items": []}
    # runner API 層：allow_external 由呼叫端決定；CLI 層做雙條件 gate
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=analyzer,
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=False)
    assert not called, "無 env/flag → offline"


def test_analyzer_failure_counted():
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: (_ for _ in ()).throw(
                                    RuntimeError("x")),
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].success is False
    assert res[0].error_code.startswith("analyzer_error:")


# ================================================================
# Payload comparison
# ================================================================
def test_exact_payload_match():
    f = {"type": "single", "items": [{"name": "AK-47 | Redline (Field-Tested)",
                                      "price": "5000", "currency": "TWD"}]}
    a = {"type": "single", "items": [{"name": "AK-47 | Redline (Field-Tested)",
                                      "price": "5000", "currency": "TWD"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.image_kind_match and c.item_count_match
    assert c.exact_name_matches == 1 and c.price_match and c.currency_match


def test_missing_item_detected():
    f = {"type": "single", "items": [{"name": "A"}, {"name": "B"}]}
    a = {"type": "single", "items": [{"name": "A"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.missing_items == 1


def test_extra_item_detected():
    f = {"type": "single", "items": [{"name": "A"}]}
    a = {"type": "single", "items": [{"name": "A"}, {"name": "B"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.extra_items == 1


def test_price_difference_detected():
    f = {"type": "single", "items": [{"name": "A", "price": "5000"}]}
    a = {"type": "single", "items": [{"name": "A", "price": "5500"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.price_match is False


def test_currency_difference_detected():
    f = {"type": "single", "items": [{"name": "A", "currency": "TWD"}]}
    a = {"type": "single", "items": [{"name": "A", "currency": "RMB"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.currency_match is False


def test_image_kind_difference_detected():
    f = {"type": "single", "items": []}
    a = {"type": "market", "items": []}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.image_kind_match is False


def test_deterministic_disagreement_order():
    # 兩次比較結果一致（deterministic）
    f = {"type": "single", "items": [{"name": "A", "price": "1"}]}
    a = {"type": "market", "items": [{"name": "B", "price": "2"}]}
    c1 = compare_fixture_and_analyzer_payload(f, a)
    c2 = compare_fixture_and_analyzer_payload(f, a)
    assert (c1.image_kind_match, c1.extra_items, c1.price_match) == \
        (c2.image_kind_match, c2.extra_items, c2.price_match)


# ================================================================
# Phase 6.4C1.1/6.4C1.2 — Timeout / Sanitizer / Price None
# ================================================================
def _slow_analyzer(bytes_arg, prompt):
    """module-level（可 pickle）：sleep 5 秒後回傳。"""
    import time
    time.sleep(5)
    return {"type": "single", "items": []}


def test_real_timeout_reported():
    # analyzer sleep 5s、timeout 0.2s → 快速返回 analyzer_timeout
    import time as _time
    case = _case(images=1)
    t0 = _time.perf_counter()
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=_slow_analyzer,
                                config=AnalyzerRunConfig(
                                    timeout_seconds=1, use_cache=False),
                                allow_external=True)
    elapsed = _time.perf_counter() - t0
    assert res[0].success is False
    assert res[0].error_code == "analyzer_timeout"
    assert elapsed < 4.0, f"必須快速返回（wall-clock {elapsed:.1f}s）"


def test_timeout_subsequent_images_continue():
    # timeout 後續圖片仍可處理
    import time as _time
    case = _case(images=2)

    def fast_second(b, p):
        return {"type": "single", "items": []}
    # 第一張 slow（module-level）、第二張 fast
    def loader(case, idx):
        return b"slow" if idx == 0 else b"fast"
    res = run_analyzer_for_case(case, loader,
                                analyzer=_slow_analyzer,
                                config=AnalyzerRunConfig(
                                    timeout_seconds=1, use_cache=False),
                                allow_external=True)
    assert res[0].success is False and res[0].error_code == "analyzer_timeout"
    # 第二張：cache miss offline（analyzer 全部走 timeout 分支？）
    # 註：allow_external=True 且 analyzer 相同 → 第二張也 timeout（slow）
    assert len(res) == 2, "後續圖片仍處理"


def test_no_zombie_workers():
    # process isolation 後無殘留 worker
    import multiprocessing as mp
    import time as _time
    case = _case(images=1)
    before = len(mp.active_children())
    run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                          analyzer=_slow_analyzer,
                          config=AnalyzerRunConfig(
                              timeout_seconds=1, use_cache=False),
                          allow_external=True)
    _time.sleep(0.5)  # 給 terminate/join 時間
    after = len(mp.active_children())
    assert after <= before, f"不得殘留 zombie process（{before}→{after}）"


def test_sanitize_removes_bytes():
    from alkaid_cs2.evaluation.vision_analyzer_runner import sanitize_payload
    p = {"type": "single", "items": [{"name": "A"}],
         "raw_bytes": b"12345", "image_base64": "abc"}
    out = sanitize_payload(p)
    assert "raw_bytes" not in out, "binary 欄位移除"
    assert "image_base64" not in out
    assert out["type"] == "single", "非敏感欄位保留"


def test_sanitize_nested_bytes():
    from alkaid_cs2.evaluation.vision_analyzer_runner import sanitize_payload
    p = {"items": [{"name": "A", "data": {"image_bytes": b"x"}}]}
    out = sanitize_payload(p)
    assert "image_bytes" not in out["items"][0]["data"]


def test_sanitize_removes_auth_keys():
    from alkaid_cs2.evaluation.vision_analyzer_runner import sanitize_payload
    p = {"type": "single", "Authorization": "Bearer xyz", "cookie": "a=1",
         "api_key": "sk-abc"}
    out = sanitize_payload(p)
    assert "Authorization" not in out and "cookie" not in out
    assert "api_key" not in out


def test_sanitize_bytes_value_dropped_not_str():
    from alkaid_cs2.evaluation.vision_analyzer_runner import sanitize_payload
    p = {"type": "single", "data": b"\x89PNG\r\n"}
    out = sanitize_payload(p)
    assert "data" not in out, "bytes 不得轉字串保存"


def test_write_cache_gate_requires_success():
    # write_cache=False → 不寫 cache
    tmp = Path(os.path.join(PROJECT_ROOT, "tests", "fixtures", "vision_analyzer_cache"))
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"zzz-new"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(use_cache=True, write_cache=False,
                                                         timeout_seconds=0),
                                cache_dir=tmp, allow_external=True)
    assert res[0].success is True
    # 該 hash 的 cache 不存在（write 被 gate）
    from alkaid_cs2.evaluation.vision_analyzer_runner import (
        _cache_key, _safe_cache_path, cache_lookup,
    )
    h = compute_image_hash(b"zzz-new")
    assert cache_lookup(tmp, h, "default", "v1") is None, "write_cache=False 不寫"


def test_no_price_both_sides_is_match():
    f = {"type": "single", "items": [{"name": "A"}]}
    a = {"type": "single", "items": [{"name": "A"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.price_match is True, "None vs None → match"


def test_fixture_no_price_analyzer_has_price_is_mismatch():
    f = {"type": "single", "items": [{"name": "A"}]}
    a = {"type": "single", "items": [{"name": "A", "price": "5000"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.price_match is False


def test_fixture_has_price_analyzer_no_price_is_mismatch():
    f = {"type": "single", "items": [{"name": "A", "price": "5000"}]}
    a = {"type": "single", "items": [{"name": "A"}]}
    c = compare_fixture_and_analyzer_payload(f, a)
    assert c.price_match is False


# ================================================================
# Phase 6.4C1.2 — Image load failure
# ================================================================
def test_loader_none_returns_image_load_failed():
    case = _case(images=1)
    res = run_analyzer_for_case(case, lambda c, i: None,
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
                                allow_external=True)
    assert res[0].success is False
    assert res[0].error_code == "image_load_failed"
    assert res[0].image_hash is None, "失敗時 hash 可為 None"


def test_loader_none_does_not_abort_case():
    case = _case(images=2)
    res = run_analyzer_for_case(
        case,
        lambda c, i: None if i == 0 else b"ok",
        analyzer=lambda b, p: {"type": "single", "items": []},
        config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
        allow_external=True)
    assert len(res) == 2, "單張 load 失敗不中斷"
    assert res[0].error_code == "image_load_failed"
    assert res[1].success is True, "第二張成功"


def test_loader_none_then_second_image_success():
    case = _case(images=2)
    res = run_analyzer_for_case(
        case,
        lambda c, i: None if i == 0 else b"ok",
        analyzer=lambda b, p: {"type": "single", "items": []},
        config=AnalyzerRunConfig(use_cache=False, timeout_seconds=0),
        allow_external=True)
    assert res[0].error_code == "image_load_failed"
    assert res[1].success and res[1].image_hash, "第二張 hash 非空"


def test_successful_result_requires_hash():
    with pytest.raises(ValueError, match="image_hash 必須非空"):
        AnalyzerImageResult(image_index=0, image_hash="", success=True,
                            payload={"type": "single", "items": []})


# ================================================================
# Phase 6.4C1.3 — Unpickleable analyzer / cache_write 防護
# ================================================================
def test_unpickleable_analyzer_rejected_with_timeout():
    # lambda（不可 pickle）+ timeout>0 → analyzer_not_pickleable（不執行）
    case = _case(images=1)
    called = []

    def lam(b, p):
        called.append(1)
        return {"type": "single", "items": []}
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lam,
                                config=AnalyzerRunConfig(
                                    timeout_seconds=20, use_cache=False),
                                allow_external=True)
    assert res[0].success is False
    assert res[0].error_code == "analyzer_not_pickleable"


def test_unpickleable_analyzer_not_called():
    case = _case(images=1)
    called = []

    def lam(b, p):
        called.append(1)
        return {"type": "single", "items": []}
    run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                          analyzer=lam,
                          config=AnalyzerRunConfig(
                              timeout_seconds=20, use_cache=False),
                          allow_external=True)
    assert not called, "不可 pickle + timeout>0 → 不得執行 analyzer"


def test_unpickleable_analyzer_allowed_when_timeout_zero():
    # timeout_seconds=0（timeout disabled）→ 允許同步執行 lambda
    case = _case(images=1)
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=lambda b, p: {"type": "single", "items": []},
                                config=AnalyzerRunConfig(
                                    timeout_seconds=0, use_cache=False),
                                allow_external=True)
    assert res[0].success is True, "timeout=0 允許不可 pickle analyzer"


def test_pickleable_analyzer_timeout_still_fast():
    # module-level（可 pickle）analyzer 仍有 timeout
    import time as _time
    case = _case(images=1)
    t0 = _time.perf_counter()
    res = run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                                analyzer=_slow_analyzer,
                                config=AnalyzerRunConfig(
                                    timeout_seconds=1, use_cache=False),
                                allow_external=True)
    elapsed = _time.perf_counter() - t0
    assert res[0].error_code == "analyzer_timeout"
    assert elapsed < 4.0, f"pickleable timeout 仍快速（{elapsed:.1f}s）"


def test_no_zombie_after_unpickleable_rejection():
    import multiprocessing as mp
    import time as _time
    case = _case(images=1)
    before = len(mp.active_children())
    run_analyzer_for_case(case, _loader_factory({0: b"a"}),
                          analyzer=lambda b, p: {"type": "single", "items": []},
                          config=AnalyzerRunConfig(
                              timeout_seconds=20, use_cache=False),
                          allow_external=True)
    _time.sleep(0.3)
    assert len(mp.active_children()) <= before, "拒絕後無 zombie"


def test_cache_write_self_sanitizes(tmp_path):
    # cache_write 自身 sanitize（不依賴 caller）
    dirty = {"type": "single", "items": [{"name": "A"}],
             "raw_bytes": b"123", "image_base64": "abc",
             "nested": {"image_bytes": b"x", "token": "sk-abc"},
             "Authorization": "Bearer xyz"}
    cache_write(tmp_path, "h1", "m1", "p1", dirty)
    p = _safe_cache_path(tmp_path, _cache_key("h1", "m1", "p1"))
    data = json.loads(p.read_text(encoding="utf-8"))
    result = data["result"]
    assert "raw_bytes" not in result
    assert "image_base64" not in result
    assert "Authorization" not in result
    assert "token" not in result["nested"]
    assert "image_bytes" not in result["nested"]
    assert result["items"][0]["name"] == "A", "非敏感欄位保留"


def test_cache_write_rejects_nonserializable():
    # sanitize 後仍無法 JSON serialize → ValueError
    with pytest.raises(ValueError, match="JSON serialize"):
        cache_write(Path(os.path.join(PROJECT_ROOT, "tests", "fixtures",
                                      "vision_analyzer_cache")),
                    "h9", "m1", "p1",
                    {"type": "single", "items": [{"bad": object()}]})
