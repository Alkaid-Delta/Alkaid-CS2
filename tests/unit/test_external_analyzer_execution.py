# -*- coding: utf-8 -*-
"""test_external_analyzer_execution.py — Phase 6.4C2-B1 execution engine"""
import hashlib
import json
import os
import re
import sys
import subprocess
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from alkaid_cs2.evaluation.analyzer_audit import (  # noqa: E402
    AUDIT_SCHEMA_VERSION_V2, KNOWN_ERROR_CODES, validate_audit_manifest,
)
from alkaid_cs2.evaluation.analyzer_cache import (  # noqa: E402
    build_cache_record, compute_analyzer_cache_key,
    load_analyzer_cache_record, validate_cache_record,
)
from alkaid_cs2.evaluation.external_analyzer_adapter import (  # noqa: E402
    FakeExternalAnalyzerAdapter, FailingExternalAnalyzerAdapter,
)
from alkaid_cs2.evaluation.external_analyzer_runner import (  # noqa: E402
    EligibleAnalyzerCase, build_execution_plan,
    execute_external_analyzer_plan,
)
from alkaid_cs2.evaluation.secure_image_loader import (  # noqa: E402
    InMemorySecureImageLoader, SecureImageLoadError,
)

FAKE_BYTES = b"fake-image-001"
FAKE_SHA = hashlib.sha256(FAKE_BYTES).hexdigest()


def _case(case_id="case-A", refs=("secure-store://img-1",),
          hashes=(FAKE_SHA,)):
    return EligibleAnalyzerCase(
        source_case_id=case_id, storage_references=list(refs),
        image_hashes=list(hashes), review_status="double_review",
        privacy_scan_status="passed", source="anonymized_real")


FAKE_ANALYZER_NAME = "fake-analyzer"
FAKE_ANALYZER_VERSION = "0.1.0"


def _plan(case, run_salt="salt-1", dry_run=False, authorized=True,
          adapter_name=FAKE_ANALYZER_NAME):
    return build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt=run_salt,
        adapter_name=adapter_name, dry_run=dry_run, authorized=authorized,
        created_at="2026-08-01T00:00:00Z")


def _env():
    td = tempfile.mkdtemp(prefix="b1-unit-")
    root = os.path.join(td, "local_data")
    cache = os.path.join(root, "cache")
    runs = os.path.join(root, "runs")
    os.makedirs(cache, exist_ok=True)
    os.makedirs(runs, exist_ok=True)
    return root, cache, runs


def _loader():
    return InMemorySecureImageLoader({"secure-store://img-1": FAKE_BYTES})


def _execute(case=None, *, plan=None, loader=None, adapter=None,
             analyzer_version=FAKE_ANALYZER_VERSION,
             analyzer_name=FAKE_ANALYZER_NAME, **kw):
    case = case or _case()
    plan = plan or _plan(case)
    root, cache, runs = _env()
    if loader is None:
        loader = _loader()
    if adapter is None:
        adapter = FakeExternalAnalyzerAdapter()
    return execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=loader, adapter=adapter,
        cache_dir=cache, audit_dir=runs, allowed_root=root,
        analyzer_name=analyzer_name,
        analyzer_version=analyzer_version, **kw)


# ---- preflight ----
def test_preflight_dry_run_plan_rejected():
    case = _case()
    plan = _plan(case, dry_run=True)
    s = _execute(case, plan=plan)
    assert s.status == "blocked"
    assert "execution_plan_dry_run_only" in s.fixed_error_codes


def test_preflight_unauthorized_rejected():
    case = _case()
    plan = _plan(case, authorized=False)
    s = _execute(case, plan=plan)
    assert s.status == "blocked"
    assert "execution_plan_not_authorized" in s.fixed_error_codes


def test_preflight_missing_loader():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=None,
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "secure_image_loader_unavailable" in s.fixed_error_codes


def test_preflight_missing_adapter():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(), adapter=None,
        cache_dir=cache, audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "analyzer_adapter_unavailable" in s.fixed_error_codes


def test_preflight_count_mismatch():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_count_mismatch" in s.fixed_error_codes


def test_preflight_output_path_not_allowed():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    outside = os.path.join(root, "..", "outside")
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=outside,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "output_path_not_allowed" in s.fixed_error_codes


def test_preflight_blocks_write_no_cache_no_audit():
    # preflight fail → 不寫 cache、可寫 blocked audit
    case = _case()
    plan = _plan(case, dry_run=True)
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert os.listdir(cache) == []
    audits = os.listdir(runs)
    assert len(audits) == 1
    a = json.load(open(os.path.join(runs, audits[0], "audit.json"),
                       encoding="utf-8"))
    assert a["result"] == "blocked"


# ---- fake loader ----
def test_fake_loader_valid_bytes_returned():
    loader = _loader()
    assert loader.load("secure-store://img-1", FAKE_SHA) == FAKE_BYTES


def test_fake_loader_missing_object_fixed_error():
    loader = _loader()
    with pytest.raises(SecureImageLoadError) as ei:
        loader.load("secure-store://missing", FAKE_SHA)
    assert "secure_image_not_found" in str(ei.value)


def test_fake_loader_hash_mismatch_fixed_error():
    loader = _loader()
    with pytest.raises(SecureImageLoadError) as ei:
        loader.load("secure-store://img-1", "0" * 64)
    assert "secure_image_hash_mismatch" in str(ei.value)


# ---- execution + cache ----
def test_first_run_cache_miss_then_write():
    s = _execute()
    assert s.status == "completed"
    assert s.cache_miss_count == 1 and s.attempted_image_count == 1
    assert s.cache_write_count == 1 and s.succeeded_image_count == 1


def test_second_run_cache_hit_no_loader_call():
    calls = {"load": 0}

    class SpyLoader(InMemorySecureImageLoader):
        def load(self, *a, **k):
            calls["load"] += 1
            return super().load(*a, **k)

    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    loader = SpyLoader({"secure-store://img-1": FAKE_BYTES})
    adapter = FakeExternalAnalyzerAdapter()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    s1 = execute_external_analyzer_plan(loader=loader, adapter=adapter, **kw)
    assert s1.succeeded_image_count == 1
    n1 = calls["load"]
    s2 = execute_external_analyzer_plan(loader=loader, adapter=adapter, **kw)
    assert s2.cache_hit_count == 1
    assert calls["load"] == n1, "cache hit 不得呼叫 loader"


def test_second_run_cache_hit_no_adapter_call():
    calls = {"analyze": 0}

    class SpyAdapter(FakeExternalAnalyzerAdapter):
        def analyze_image(self, *a, **k):
            calls["analyze"] += 1
            return super().analyze_image(*a, **k)

    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    adapter = SpyAdapter()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    s1 = execute_external_analyzer_plan(loader=_loader(), adapter=adapter, **kw)
    n1 = calls["analyze"]
    s2 = execute_external_analyzer_plan(loader=_loader(), adapter=adapter, **kw)
    assert s2.cache_hit_count == 1
    assert calls["analyze"] == n1, "cache hit 不得呼叫 adapter"


def test_analyzer_version_change_same_cache_miss(tmp_path):
    # B1.1：adapter 換 version（identity 合法）→ 共用 cache → cache key 變 → miss
    class V2Adapter(FakeExternalAnalyzerAdapter):
        analyzer_version = "2.0.0"

    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME)
    s1 = execute_external_analyzer_plan(
        loader=_loader(), adapter=FakeExternalAnalyzerAdapter(),
        analyzer_version="0.1.0", **kw)
    s2 = execute_external_analyzer_plan(
        loader=_loader(), adapter=V2Adapter(), analyzer_version="2.0.0", **kw)
    assert s1.status == "completed" and s1.cache_miss_count == 1
    assert s2.status == "completed" and s2.cache_miss_count == 1 \
        and s2.cache_hit_count == 0


def test_image_index_change_cache_miss():
    # 多圖 case：換 image index 視為不同 item
    case2 = _case(hashes=(FAKE_SHA, hashlib.sha256(b"x").hexdigest()),
                  refs=("secure-store://img-1", "secure-store://img-2"))
    loader2 = InMemorySecureImageLoader(
        {"secure-store://img-1": FAKE_BYTES, "secure-store://img-2": b"x"})
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    kw = dict(cache_dir=cache, audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    s1 = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=loader2,
        adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s1.succeeded_image_count == 1
    # 同一 case 但 image 數不同 → plan 不同（此處驗證 cache key 含 index）
    ck1 = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    ck2 = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=1,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert ck1 != ck2, "image index 必須影響 cache key"


def test_corrupted_cache_rejected():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    # 先寫合法 cache，再破壞
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    f = os.path.join(cache, f"{ck}.json")
    with open(f, "w", encoding="utf-8") as fh:
        fh.write("{broken json")
    rec, errs = load_analyzer_cache_record(
        ck, cache, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert rec is None and "cache_read_failed" in errs


def test_result_hash_mismatch_rejected():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    f = os.path.join(cache, f"{ck}.json")
    rec = json.load(open(f, encoding="utf-8"))
    rec["result_sha256"] = "0" * 64
    json.dump(rec, open(f, "w", encoding="utf-8"))
    loaded, errs = load_analyzer_cache_record(
        ck, cache, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    # B1.1 錯誤正規化：不得暴露 result_sha256_mismatch 等 schema detail
    assert loaded is None
    assert errs == ["cache_record_invalid"]


def test_cache_hit_result_equal_original_result():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    rec, errs = load_analyzer_cache_record(
        ck, cache, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert rec is not None and errs == []
    assert rec["normalized_result"]["items"][0]["name"] == "fake-item-001"
    assert validate_cache_record(rec) == []


# ---- failure containment ----
def test_one_image_failure_does_not_stop_next_image():
    case2 = _case(case_id="case-B",
                  hashes=(FAKE_SHA, hashlib.sha256(b"y").hexdigest()),
                  refs=("secure-store://img-1", "secure-store://img-missing"))
    plan = build_execution_plan(
        eligible_cases=[case2.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    loader = InMemorySecureImageLoader({"secure-store://img-1": FAKE_BYTES})
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case2], loader=loader,
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed_with_failures"
    assert s.succeeded_image_count == 1 and s.failed_image_count == 1
    assert "secure_image_not_found" in s.fixed_error_codes


def test_adapter_failure_contained():
    case = _case()
    plan = _plan(case, adapter_name="failing-analyzer")
    s = _execute(case, plan=plan, adapter=FailingExternalAnalyzerAdapter(),
                 analyzer_version="0.1.0", analyzer_name="failing-analyzer")
    assert s.status == "failed"
    assert s.failed_image_count == 1
    assert "analyzer_execution_failed" in s.fixed_error_codes


def test_invalid_result_contained():
    class BadAdapter(FakeExternalAnalyzerAdapter):
        def analyze_image(self, *a, **k):
            return {"kind": "image", "item_count": 1, "items": []}  # mismatch

    s = _execute(adapter=BadAdapter())
    assert s.status == "failed"
    assert "analyzer_result_invalid" in s.fixed_error_codes
    assert s.failed_image_count == 1


def test_failed_item_does_not_create_cache():
    case = _case()
    plan = _plan(case, adapter_name="failing-analyzer")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FailingExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name="failing-analyzer",
        analyzer_version="0.1.0")
    assert os.listdir(cache) == [], "失敗 item 不得建立 cache"
    assert s.cache_write_count == 0


def test_all_images_failed_status_failed():
    case = _case()
    plan = _plan(case, adapter_name="failing-analyzer")
    s = _execute(case, plan=plan, adapter=FailingExternalAnalyzerAdapter(),
                 analyzer_version="0.1.0", analyzer_name="failing-analyzer")
    assert s.status == "failed"


def test_partial_failure_status_completed_with_failures():
    case2 = _case(case_id="case-C",
                  hashes=(FAKE_SHA, hashlib.sha256(b"z").hexdigest()),
                  refs=("secure-store://img-1", "secure-store://img-2"))
    plan = build_execution_plan(
        eligible_cases=[case2.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    loader = InMemorySecureImageLoader(
        {"secure-store://img-1": FAKE_BYTES, "secure-store://img-2": b"z"})

    class HalfFailing(FakeExternalAnalyzerAdapter):
        def analyze_image(self, image_bytes, *, case_key, image_index):
            if image_index == 1:
                raise RuntimeError("simulated")
            return super().analyze_image(
                image_bytes, case_key=case_key, image_index=image_index)

    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case2], loader=loader,
        adapter=HalfFailing(), cache_dir=cache, audit_dir=runs,
        allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed_with_failures"
    assert s.succeeded_image_count == 1 and s.failed_image_count == 1


def test_fixed_errors_only_no_exception_text():
    case = _case()
    plan = _plan(case, adapter_name="failing-analyzer")
    s = _execute(case, plan=plan, adapter=FailingExternalAnalyzerAdapter(),
                 analyzer_version="0.1.0", analyzer_name="failing-analyzer")
    for code in s.fixed_error_codes:
        assert "RuntimeError" not in code
        assert "simulated" not in code
        assert "crashed" not in code


# ---- audit v2 ----
def test_audit_v2_valid_record():
    case = _case()
    plan = _plan(case)
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    audits = os.listdir(runs)
    a = json.load(open(os.path.join(runs, audits[0], "audit.json"),
                       encoding="utf-8"))
    assert a["schema_version"] == AUDIT_SCHEMA_VERSION_V2
    assert validate_audit_manifest(a) == []
    assert a["processed_image_count"] == 1
    assert a["attempted_image_count"] == 1
    assert a["cache_hit_count"] == 0 and a["cache_miss_count"] == 1



def test_audit_v2_sensitive_value_rejected():
    a = {
        "schema_version": AUDIT_SCHEMA_VERSION_V2, "run_id": "run-" + "a" * 12,
        "started_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:00:01Z", "dry_run": False,
        "authorization_flag_present": True, "authorization_env_present": True,
        "eligible_case_count": 1, "eligible_image_count": 1,
        "processed_image_count": 1, "attempted_image_count": 1,
        "succeeded_image_count": 1, "failed_image_count": 0,
        "cache_hit_count": 0, "cache_miss_count": 1, "cache_write_count": 1,
        "result": "completed", "fixed_error_codes": [],
        "image_hash_hashes": ["a" * 64],
        "analyzer_name": "fake", "analyzer_version": "1.0.0",
        "token": "sk-secret",  # 未知欄位 + 敏感
    }
    errs = validate_audit_manifest(a)
    assert any("unknown_fields" in e for e in errs)
    assert any("privacy" in e for e in errs)


# ---- determinism ----
def test_identical_run_semantics_deterministic():
    case = _case()
    s1 = _execute()
    s2 = _execute()
    d1 = s1.to_dict()
    d2 = s2.to_dict()
    for k in ("planned_image_count", "processed_image_count",
              "attempted_image_count", "succeeded_image_count",
              "failed_image_count", "cache_hit_count", "cache_miss_count",
              "cache_write_count", "fixed_error_codes", "status"):
        assert d1[k] == d2[k], f"{k} 必須一致"
    assert d1["run_id"] != d2["run_id"], "run_id 是唯一預期差異"


def test_result_same_process_deterministic():
    adapter = FakeExternalAnalyzerAdapter()
    r1 = adapter.analyze_image(FAKE_BYTES, case_key="k" * 64, image_index=0)
    r2 = adapter.analyze_image(FAKE_BYTES, case_key="k" * 64, image_index=0)
    assert r1 == r2


def test_cache_key_same_process_deterministic():
    ck1 = compute_analyzer_cache_key(
        opaque_case_key="k" * 64, image_index=0, image_sha256=FAKE_SHA,
        analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    ck2 = compute_analyzer_cache_key(
        opaque_case_key="k" * 64, image_index=0, image_sha256=FAKE_SHA,
        analyzer_name=FAKE_ANALYZER_NAME, analyzer_version=FAKE_ANALYZER_VERSION)
    assert ck1 == ck2


def test_summary_no_sensitive_fields():
    s = _execute()
    d = s.to_dict()
    raw = json.dumps(d)
    assert "case" not in raw.lower() or "opaque" in raw
    assert "secure-store://" not in raw
    assert "fake-image" not in raw  # bytes 內容
    assert "RuntimeError" not in raw


# ================================================================
# Phase 6.4C2-B1.1 — Multi-case plan / accounting / identity binding
# ================================================================
def test_two_single_image_cases_preflight_allowed(tmp_path):
    # case A [0] + case B [0] → 扁平 [0, 0] 合法
    case_a = _case(case_id="case-A")
    case_b = _case(case_id="case-B")
    plan = build_execution_plan(
        eligible_cases=[case_a.as_dict(), case_b.as_dict()],
        run_salt="salt-1", adapter_name=FAKE_ANALYZER_NAME,
        dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    loader = InMemorySecureImageLoader(
        {"secure-store://img-1": FAKE_BYTES})
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case_a, case_b], loader=loader,
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed"
    assert s.succeeded_image_count == 2
    assert plan.image_indexes == [0, 0]


def test_multi_case_multi_image_indexes_match(tmp_path):
    # case A 2 圖 + case B 1 圖 → [0, 1, 0]
    case_a = _case(case_id="case-A",
                   refs=("secure-store://img-1", "secure-store://img-2"),
                   hashes=(FAKE_SHA, hashlib.sha256(b"x").hexdigest()))
    case_b = _case(case_id="case-B")
    plan = build_execution_plan(
        eligible_cases=[case_a.as_dict(), case_b.as_dict()],
        run_salt="salt-1", adapter_name=FAKE_ANALYZER_NAME,
        dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    assert plan.image_indexes == [0, 1, 0]
    assert plan.image_count == 3
    assert len(plan.case_keys) == 3
    assert plan.case_keys[0] == plan.case_keys[1] != plan.case_keys[2]
    root, cache, runs = _env()
    loader = InMemorySecureImageLoader(
        {"secure-store://img-1": FAKE_BYTES,
         "secure-store://img-2": b"x"})
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case_a, case_b], loader=loader,
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed"
    assert s.succeeded_image_count == 3


def test_same_image_index_different_case_keys_not_duplicate(tmp_path):
    # (keyA, 0) 與 (keyB, 0) 不算 duplicate
    case_a = _case(case_id="case-A")
    case_b = _case(case_id="case-B")
    plan = build_execution_plan(
        eligible_cases=[case_a.as_dict(), case_b.as_dict()],
        run_salt="salt-1", adapter_name=FAKE_ANALYZER_NAME,
        dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case_a, case_b],
        loader=InMemorySecureImageLoader(
            {"secure-store://img-1": FAKE_BYTES}),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed"
    assert "duplicate_execution_item" not in s.fixed_error_codes


# ---- accounting：loader 失敗不算 adapter invocation ----
def test_loader_missing_does_not_increment_adapter_attempted(tmp_path):
    calls = {"analyze": 0}

    class SpyAdapter(FakeExternalAnalyzerAdapter):
        def analyze_image(self, *a, **k):
            calls["analyze"] += 1
            return super().analyze_image(*a, **k)

    case = _case(case_id="case-L")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case],
        loader=InMemorySecureImageLoader({}),  # object 缺失
        adapter=SpyAdapter(), cache_dir=cache, audit_dir=runs,
        allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "failed"
    assert s.failed_image_count == 1
    assert s.attempted_image_count == 0, "loader 失敗不得增加 attempted"
    assert calls["analyze"] == 0
    assert "secure_image_not_found" in s.fixed_error_codes


def test_loader_hash_mismatch_fixed_code(tmp_path):
    case = _case(case_id="case-H")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case],
        loader=InMemorySecureImageLoader(
            {"secure-store://img-1": b"different-bytes"}),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert "secure_image_hash_mismatch" in s.fixed_error_codes
    assert s.attempted_image_count == 0


def test_loader_invalid_reference_fixed_code(tmp_path):
    case = _case(case_id="case-R", refs=("http://evil/x",))
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case],
        loader=InMemorySecureImageLoader({}),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert "secure_reference_invalid" in s.fixed_error_codes


def test_unknown_loader_exception_fixed_code(tmp_path):
    class WeirdLoader:
        def load(self, ref, expected_sha256):
            raise RuntimeError("boom-loader")

    case = _case(case_id="case-W")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=WeirdLoader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "failed"
    assert "secure_image_loader_failed" in s.fixed_error_codes
    assert "boom-loader" not in str(s.fixed_error_codes)
    assert s.attempted_image_count == 0


# ---- corrupted cache accounting ----
def test_corrupt_json_cache_counted_invalid(tmp_path):
    case = _case(case_id="case-J")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    with open(os.path.join(cache, f"{ck}.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{broken")
    s2 = execute_external_analyzer_plan(
        loader=_loader(), adapter=FakeExternalAnalyzerAdapter(), **kw)
    # 單圖全部失敗 → status failed
    assert s2.status == "failed"
    assert s2.cache_invalid_count == 1 and s2.failed_image_count == 1
    assert s2.cache_miss_count == 0 and s2.attempted_image_count == 0
    assert "cache_read_failed" in s2.fixed_error_codes


def test_cache_result_hash_mismatch_counted_invalid(tmp_path):
    case = _case(case_id="case-HM")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    f = os.path.join(cache, f"{ck}.json")
    rec = json.load(open(f, encoding="utf-8"))
    rec["result_sha256"] = "0" * 64
    json.dump(rec, open(f, "w", encoding="utf-8"))
    s2 = execute_external_analyzer_plan(
        loader=_loader(), adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s2.status == "failed"  # 單圖全失敗
    assert s2.cache_invalid_count == 1
    assert "cache_record_invalid" in s2.fixed_error_codes
    # 不暴露 schema detail
    assert not any("result_sha256" in c for c in s2.fixed_error_codes)


def test_cache_schema_invalid_not_exposed(tmp_path):
    case = _case(case_id="case-S")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    f = os.path.join(cache, f"{ck}.json")
    rec = json.load(open(f, encoding="utf-8"))
    rec["normalized_result"] = {"unknown_field": 1}
    json.dump(rec, open(f, "w", encoding="utf-8"))
    s2 = execute_external_analyzer_plan(
        loader=_loader(), adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s2.cache_invalid_count == 1
    assert "cache_record_invalid" in s2.fixed_error_codes
    for c in s2.fixed_error_codes:
        assert "unknown_fields" not in c and "privacy" not in c


def test_corrupt_cache_still_writes_valid_audit(tmp_path):
    case = _case(case_id="case-AUD")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    execute_external_analyzer_plan(loader=_loader(),
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    ck = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[0], image_index=0,
        image_sha256=FAKE_SHA, analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    with open(os.path.join(cache, f"{ck}.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{broken")
    s2 = execute_external_analyzer_plan(
        loader=_loader(), adapter=FakeExternalAnalyzerAdapter(), **kw)
    from alkaid_cs2.evaluation.analyzer_audit import validate_audit_manifest
    # B1.2：run_id 統一 = plan.run_id → 兩次執行共用同一 audit 目錄（覆寫）
    audits = os.listdir(runs)
    assert audits == [plan.run_id], f"audit 目錄必須用 plan.run_id：{audits}"
    a2 = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                        encoding="utf-8"))
    assert a2["cache_invalid_count"] == 1  # 第二次執行（corrupted）覆寫
    assert validate_audit_manifest(a2) == []
    assert a2["result"] == "failed"  # 單圖全失敗
    assert a2["cache_invalid_count"] == 1
    assert a2["processed_image_count"] == 1
    assert a2["attempted_image_count"] == 0


def test_processed_equals_hits_plus_misses_plus_invalid(tmp_path):
    # 2 圖：一張 hit、一張 invalid、一張 miss 無法同時——用 3 圖 case
    case3 = _case(case_id="case-P",
                  refs=("secure-store://img-1", "secure-store://img-2",
                        "secure-store://img-3"),
                  hashes=(FAKE_SHA, hashlib.sha256(b"x").hexdigest(),
                          hashlib.sha256(b"y").hexdigest()))
    plan = build_execution_plan(
        eligible_cases=[case3.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    kw = dict(plan=plan, eligible_cases=[case3], cache_dir=cache,
              audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    loader = InMemorySecureImageLoader(
        {"secure-store://img-1": FAKE_BYTES,
         "secure-store://img-2": b"x", "secure-store://img-3": b"y"})
    execute_external_analyzer_plan(loader=loader,
                                   adapter=FakeExternalAnalyzerAdapter(), **kw)
    # 破壞 img-2 cache → invalid；img-1 保留 → hit；刪 img-3 cache → miss
    ck2 = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[1], image_index=1,
        image_sha256=hashlib.sha256(b"x").hexdigest(),
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    with open(os.path.join(cache, f"{ck2}.json"), "w",
              encoding="utf-8") as fh:
        fh.write("{broken")
    ck3 = compute_analyzer_cache_key(
        opaque_case_key=plan.case_keys[2], image_index=2,
        image_sha256=hashlib.sha256(b"y").hexdigest(),
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    os.remove(os.path.join(cache, f"{ck3}.json"))
    s2 = execute_external_analyzer_plan(
        loader=loader, adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s2.processed_image_count == 3
    assert s2.cache_hit_count == 1
    assert s2.cache_invalid_count == 1
    assert s2.cache_miss_count == 1
    assert s2.processed_image_count == \
        s2.cache_hit_count + s2.cache_miss_count + s2.cache_invalid_count
    assert s2.attempted_image_count <= s2.cache_miss_count
    assert s2.succeeded_image_count + s2.failed_image_count == \
        s2.processed_image_count


# ---- adapter identity binding ----
def test_adapter_name_mismatch_blocked(tmp_path):
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name="some-other-analyzer",
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_adapter_identity_mismatch" in s.fixed_error_codes


def test_adapter_version_mismatch_blocked(tmp_path):
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME, analyzer_version="9.9.9")
    assert s.status == "blocked"
    assert "execution_adapter_identity_mismatch" in s.fixed_error_codes


def test_plan_adapter_name_mismatch_blocked(tmp_path):
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name="different-plan-name", dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_adapter_identity_mismatch" in s.fixed_error_codes


def test_identity_mismatch_zero_loader_adapter_calls(tmp_path):
    calls = {"load": 0, "analyze": 0}

    class SpyLoader(InMemorySecureImageLoader):
        def load(self, *a, **k):
            calls["load"] += 1
            return super().load(*a, **k)

    class SpyAdapter(FakeExternalAnalyzerAdapter):
        def analyze_image(self, *a, **k):
            calls["analyze"] += 1
            return super().analyze_image(*a, **k)

    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case],
        loader=SpyLoader({"secure-store://img-1": FAKE_BYTES}),
        adapter=SpyAdapter(), cache_dir=cache, audit_dir=runs,
        allowed_root=root, analyzer_name="wrong-name",
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert calls["load"] == 0 and calls["analyze"] == 0
    assert os.listdir(cache) == []
    # blocked audit 合法寫出
    audits = os.listdir(runs)
    assert len(audits) == 1
    from alkaid_cs2.evaluation.analyzer_audit import validate_audit_manifest
    a = json.load(open(os.path.join(runs, audits[0], "audit.json"),
                       encoding="utf-8"))
    assert validate_audit_manifest(a) == []
    assert a["result"] == "blocked"


# ---- audit write failure ----
def test_preflight_audit_write_failure_reported(tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.analyzer_audit as aud
    real_write = aud.write_audit_manifest

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(aud, "write_audit_manifest", boom)
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name="wrong", dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "audit_write_failed" in s.fixed_error_codes
    assert "disk full" not in str(s.fixed_error_codes)


def test_execution_audit_write_failure_reported(tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.analyzer_audit as aud
    real_write = aud.write_audit_manifest
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise OSError("disk full")

    monkeypatch.setattr(aud, "write_audit_manifest", boom)
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed_with_failures"  # audit 失敗降級
    assert "audit_write_failed" in s.fixed_error_codes
    assert s.succeeded_image_count == 1, "cache 已寫入不受 audit 失敗影響"
    assert s.cache_write_count == 1


# ================================================================
# Phase 6.4C2-B1.1 — Cross-process determinism（subprocess）
# ================================================================
def _subprocess_compute(kind: str) -> str:
    """獨立 subprocess 計算 result hash 或 cache key（跨 process 驗證）。"""
    code = (
        "import hashlib, json, sys\n"
        "sys.path.insert(0, '.')\n"
        "from alkaid_cs2.evaluation.external_analyzer_adapter import "
        "FakeExternalAnalyzerAdapter\n"
        "from alkaid_cs2.evaluation.analyzer_cache import "
        "compute_analyzer_cache_key\n"
        "FAKE = b'fake-image-001'\n"
        "sha = hashlib.sha256(FAKE).hexdigest()\n"
        "if sys.argv[1] == 'result':\n"
        "    r = FakeExternalAnalyzerAdapter().analyze_image("
        "FAKE, case_key='k' * 64, image_index=0)\n"
        "    canonical = json.dumps(r, sort_keys=True, separators=(',', ':'))"
        ".encode('utf-8')\n"
        "    print(hashlib.sha256(canonical).hexdigest())\n"
        "else:\n"
        "    ck = compute_analyzer_cache_key("
        "opaque_case_key='k' * 64, image_index=0, image_sha256=sha,"
        "analyzer_name='fake-analyzer', analyzer_version='0.1.0')\n"
        "    print(ck)\n"
    )
    r = subprocess.run(
        [sys.executable, "-c", code, kind],
        capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=60)
    assert r.returncode == 0, r.stderr[-300:]
    return r.stdout.strip()


def test_result_hash_stable_across_subprocesses():
    out1 = _subprocess_compute("result")
    out2 = _subprocess_compute("result")
    # B1.2：subprocess 輸出的是 canonical result 的 SHA-256（非 JSON）
    assert out1 == out2, "result hash 跨 process 必須一致"
    assert len(out1) == 64, "hash 必須 64 位"
    assert all(c in "0123456789abcdef" for c in out1), "必須小寫 hex"


def test_cache_key_stable_across_subprocesses():
    ck1 = _subprocess_compute("cache_key")
    ck2 = _subprocess_compute("cache_key")
    assert ck1 == ck2
    assert len(ck1) == 64


# ================================================================
# Phase 6.4C2-B1.2 — Timestamp integrity / run_id contract
# ================================================================
def test_execution_audit_completed_at_generated_after_processing(
        tmp_path, monkeypatch):
    # monkeypatch _now_utc 依序回傳不同時間：started=T1、completed=T2>T1
    import alkaid_cs2.evaluation.external_analyzer_runner as runner
    # started=01s、analyzed_at=03s、completed=05s（analyzed_at 也會消耗）
    times = iter(["2026-08-01T00:00:01Z", "2026-08-01T00:00:03Z",
                  "2026-08-01T00:00:05Z"])
    monkeypatch.setattr(runner, "_now_utc", lambda: next(times))
    case = _case(case_id="case-T1")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    a = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["started_at"] == "2026-08-01T00:00:01Z"
    assert a["completed_at"] == "2026-08-01T00:00:05Z"
    assert a["completed_at"] > a["started_at"]


def test_blocked_audit_completed_at_generated_at_write_time(
        tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.external_analyzer_runner as runner
    # blocked 情境：started=02s、completed=09s（無 analyzed_at）
    times = iter(["2026-08-01T00:00:02Z", "2026-08-01T00:00:09Z"])
    monkeypatch.setattr(runner, "_now_utc", lambda: next(times))
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name="wrong-name", dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    a = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["completed_at"] == "2026-08-01T00:00:09Z", \
        "blocked audit completed_at 必須在寫入時產生"


def test_audit_completed_at_not_before_started_at():
    # 正常執行（無 monkeypatch）：completed >= started（字串比較同格式）
    case = _case(case_id="case-T2")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    a = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["completed_at"] >= a["started_at"]


def test_execution_summary_run_id_equals_plan_run_id():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.run_id == plan.run_id


def test_execution_audit_run_id_equals_plan_run_id():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    a = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["run_id"] == plan.run_id


def test_audit_directory_uses_plan_run_id():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert os.listdir(runs) == [plan.run_id]
    assert plan.cache_namespace == f"namespace-{plan.run_id[4:]}"


def test_invalid_plan_run_id_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "not-a-run-id"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_invalid_plan_created_at_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.created_at = "yesterday"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


# ================================================================
# Phase 6.4C2-B1.2 — Non-vacuous cache / missing failure coverage
# ================================================================
def test_successful_item_still_creates_cache(tmp_path):
    # B1.2：真正檢查 cache 目錄（非只查 summary）
    case = _case(case_id="case-SC")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed"
    assert s.cache_write_count == 1
    files = os.listdir(cache)
    assert len(files) == 1, "exactly one JSON"
    rec = json.load(open(os.path.join(cache, files[0]), encoding="utf-8"))
    assert validate_cache_record(rec) == []
    assert rec["status"] == "success"
    raw = json.dumps(rec)
    assert "secure-store://" not in raw
    assert "fake-image-001" not in raw
    assert "case-SC" not in raw


def test_image_hash_change_same_cache_miss(tmp_path):
    # 同一 opaque key/index/name/version/cache dir，只改 image SHA → miss
    bytes_a = b"fake-image-001"
    bytes_b = b"fake-image-002"
    sha_a = hashlib.sha256(bytes_a).hexdigest()
    sha_b = hashlib.sha256(bytes_b).hexdigest()
    case_a = _case(case_id="case-SHA", hashes=(sha_a,))
    case_b = _case(case_id="case-SHA", hashes=(sha_b,))
    plan_a = build_execution_plan(
        eligible_cases=[case_a.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan_b = build_execution_plan(
        eligible_cases=[case_b.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    # run_id 是 UUID（不參與 cache identity）；同 salt → 同 opaque case key
    assert plan_a.case_keys == plan_b.case_keys
    root, cache, runs = _env()
    kw = dict(cache_dir=cache, audit_dir=runs, allowed_root=root,
              analyzer_name=FAKE_ANALYZER_NAME,
              analyzer_version=FAKE_ANALYZER_VERSION)
    s1 = execute_external_analyzer_plan(
        plan=plan_a, eligible_cases=[case_a],
        loader=InMemorySecureImageLoader({"secure-store://img-1": bytes_a}),
        adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s1.status == "completed" and s1.cache_miss_count == 1
    s2 = execute_external_analyzer_plan(
        plan=plan_b, eligible_cases=[case_b],
        loader=InMemorySecureImageLoader({"secure-store://img-1": bytes_b}),
        adapter=FakeExternalAnalyzerAdapter(), **kw)
    assert s2.cache_hit_count == 0, "SHA 變 → 不得 hit"
    assert s2.cache_miss_count == 1
    assert s2.attempted_image_count == 1
    assert s2.cache_write_count == 1
    assert s2.status == "completed"


def test_cache_write_failure_contained(tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.analyzer_cache as cache_mod
    real_write = cache_mod.write_analyzer_cache_record

    def boom(*a, **k):
        raise OSError("simulated disk write failure")

    monkeypatch.setattr(cache_mod, "write_analyzer_cache_record", boom)
    case = _case(case_id="case-CW")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "failed"
    assert s.failed_image_count == 1 and s.succeeded_image_count == 0
    assert s.cache_write_count == 0
    assert "cache_write_failed" in s.fixed_error_codes
    assert "simulated" not in str(s.fixed_error_codes)
    assert "disk" not in str(s.fixed_error_codes)


def test_cache_write_failure_does_not_stop_next_image(tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.analyzer_cache as cache_mod
    real_write = cache_mod.write_analyzer_cache_record
    write_calls = {"n": 0}

    def boom(*a, **k):
        write_calls["n"] += 1
        if write_calls["n"] == 1:
            raise OSError("simulated disk write failure")
        return real_write(*a, **k)

    monkeypatch.setattr(cache_mod, "write_analyzer_cache_record", boom)
    case2 = _case(case_id="case-CW2",
                  refs=("secure-store://img-1", "secure-store://img-2"),
                  hashes=(FAKE_SHA, hashlib.sha256(b"x").hexdigest()))
    plan = build_execution_plan(
        eligible_cases=[case2.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case2],
        loader=InMemorySecureImageLoader(
            {"secure-store://img-1": FAKE_BYTES,
             "secure-store://img-2": b"x"}),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "completed_with_failures"
    assert s.failed_image_count == 1 and s.succeeded_image_count == 1
    assert s.cache_write_count == 1  # 第二張成功寫入
    assert "cache_write_failed" in s.fixed_error_codes


def test_cache_write_failure_still_writes_valid_audit(tmp_path, monkeypatch):
    import alkaid_cs2.evaluation.analyzer_cache as cache_mod
    from alkaid_cs2.evaluation.analyzer_audit import validate_audit_manifest
    real_write = cache_mod.write_analyzer_cache_record

    def boom(*a, **k):
        raise OSError("simulated")

    monkeypatch.setattr(cache_mod, "write_analyzer_cache_record", boom)
    case = _case(case_id="case-CW3")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "failed"
    audits = os.listdir(runs)
    assert audits == [plan.run_id]
    a = json.load(open(os.path.join(runs, plan.run_id, "audit.json"),
                       encoding="utf-8"))
    assert validate_audit_manifest(a) == []
    assert "cache_write_failed" in a["fixed_error_codes"]
    assert a["failed_image_count"] == 1


# ---- audit relationship 精確測試（各單一錯誤）----
def _base_v2_audit(**over):
    a = {
        "schema_version": AUDIT_SCHEMA_VERSION_V2, "run_id": "run-" + "a" * 12,
        "started_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:00:01Z", "dry_run": False,
        "authorization_flag_present": True, "authorization_env_present": True,
        "eligible_case_count": 1, "eligible_image_count": 2,
        "processed_image_count": 2, "attempted_image_count": 1,
        "succeeded_image_count": 2, "failed_image_count": 0,
        "cache_hit_count": 1, "cache_miss_count": 1,
        "cache_invalid_count": 0, "cache_write_count": 1,
        "result": "completed", "fixed_error_codes": [],
        "image_hash_hashes": ["a" * 64],
        "analyzer_name": "fake-analyzer", "analyzer_version": "0.1.0",
    }
    a.update(over)
    return a


def test_audit_v2_count_relationship_rejected():
    # 只製造 processed 不一致（cache_invalid_count 保留 0）
    a = _base_v2_audit(processed_image_count=3)
    errs = validate_audit_manifest(a)
    assert "processed_neq_hits_plus_misses_plus_invalid" in errs, errs


def test_audit_v2_attempted_exceeds_misses_rejected():
    a = _base_v2_audit(attempted_image_count=2)  # misses=1 → attempted 2 超
    errs = validate_audit_manifest(a)
    assert "attempted_exceeds_misses" in errs, errs


def test_audit_v2_succeeded_failed_relationship_rejected():
    a = _base_v2_audit(succeeded_image_count=1)  # 1+0 != processed 2
    errs = validate_audit_manifest(a)
    assert "succeeded_failed_neq_processed" in errs, errs


def test_audit_v2_cache_write_exceeds_succeeded_rejected():
    a = _base_v2_audit(cache_write_count=3)  # > succeeded 2
    errs = validate_audit_manifest(a)
    assert "cache_write_exceeds_succeeded" in errs, errs


# ================================================================
# Phase 6.4C2-B1.3 — Strict plan identity / safe trace / duplicates
# ================================================================
def test_invalid_plan_created_at_calendar_date_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.created_at = "2026-99-99T99:99:99Z"  # 不存在的日期
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_invalid_plan_created_at_hour_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.created_at = "2026-02-31T25:61:61Z"  # 不存在的時分
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_invalid_plan_created_at_minute_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.created_at = "2026-08-01T00:61:00Z"  # 分鐘 61
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_invalid_plan_created_at_non_string_blocked():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.created_at = 20260801  # 非字串
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_valid_plan_created_at_leap_day_allowed():
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        _is_valid_utc_timestamp,
    )
    assert _is_valid_utc_timestamp("2024-02-29T12:00:00Z") is True  # 閏年
    assert _is_valid_utc_timestamp("2026-02-29T12:00:00Z") is False  # 非閏年
    assert _is_valid_utc_timestamp("2026-08-01T00:00:00Z") is True
    assert _is_valid_utc_timestamp("2026-08-01T00:00:00+08:00") is False
    assert _is_valid_utc_timestamp("2026-08-01T00:00:00.123Z") is False
    assert _is_valid_utc_timestamp(12345) is False


# ---- invalid plan.run_id safe trace contract ----
def test_invalid_plan_run_id_not_exposed_in_summary():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "EVIL-../../etc/passwd"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    assert "EVIL-../../etc/passwd" not in s.run_id
    assert re.match(r"^run-[0-9a-f]{12}$", s.run_id), "必須是安全 trace ID"
    raw = json.dumps(s.to_dict())
    assert "EVIL" not in raw and "../../" not in raw, "不得回顯原始值"


def test_invalid_plan_run_id_writes_safe_blocked_audit():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "EVIL-../../etc/passwd"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    dirs = os.listdir(runs)
    assert len(dirs) == 1 and re.match(r"^run-[0-9a-f]{12}$", dirs[0])
    a = json.load(open(os.path.join(runs, dirs[0], "audit.json"),
                       encoding="utf-8"))
    assert a["run_id"] == dirs[0] == s.run_id
    assert "EVIL" not in json.dumps(a)


def test_invalid_plan_run_id_does_not_create_untrusted_directory():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "../../escape-dir"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    # audit root 外不得有 escape-dir
    outside = os.path.join(root, "..", "escape-dir")
    assert not os.path.exists(outside), "path traversal 不得逃逸 audit root"
    assert os.listdir(runs) == [s.run_id]


def test_invalid_plan_run_id_does_not_add_spurious_audit_write_failed():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "not-a-run-id"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert "execution_plan_invalid" in s.fixed_error_codes
    assert "audit_write_failed" not in s.fixed_error_codes, \
        "schema validation failure 不得誤報成 audit_write_failed"
    a = json.load(open(os.path.join(runs, s.run_id, "audit.json"),
                       encoding="utf-8"))
    assert "audit_write_failed" not in a["fixed_error_codes"]


def test_path_traversal_plan_run_id_cannot_escape_audit_root():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "run-../../.."
    root, cache, runs = _env()
    parent_before = set(os.listdir(os.path.dirname(runs)))
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.status == "blocked"
    # 所有 audit 目錄都在 runs root 內
    for d in os.listdir(runs):
        assert os.path.dirname(os.path.abspath(
            os.path.join(runs, d))) == os.path.abspath(runs)
    # runs root 外（local_data 層）不得新增任何目錄（無逃逸痕跡）
    parent_after = set(os.listdir(os.path.dirname(runs)))
    assert parent_after == parent_before, \
        f"traversal 不得在 root 外建立目錄：{parent_after - parent_before}"


def test_valid_plan_run_id_preserved_exactly():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.run_id == plan.run_id
    assert os.listdir(runs) == [plan.run_id]


def test_started_at_generated_before_preflight(tmp_path, monkeypatch):
    # 事件順序：started（_now_utc）必須在 preflight 之前
    import alkaid_cs2.evaluation.external_analyzer_runner as runner
    events = []

    def spy_now():
        events.append("now")
        return "2026-08-01T00:00:00Z"

    real_preflight = runner._execution_preflight

    def spy_preflight(**kw):
        events.append("preflight")
        return real_preflight(**kw)

    monkeypatch.setattr(runner, "_now_utc", spy_now)
    monkeypatch.setattr(runner, "_execution_preflight", spy_preflight)
    case = _case(case_id="case-ORD")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    root, cache, runs = _env()
    execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert events[0] == "now", "started_at 必須在 preflight 前產生"
    assert "preflight" in events


# ---- duplicate test-name AST audit ----
def _find_duplicate_test_names(source: str) -> list:
    import ast
    tree = ast.parse(source)
    names = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            names.append(node.name)
    from collections import Counter
    return sorted(n for n, c in Counter(names).items() if c > 1)


def test_no_duplicate_test_function_names():
    files = [
        os.path.join(PROJECT_ROOT, "tests", "unit",
                     "test_external_analyzer_execution.py"),
        os.path.join(PROJECT_ROOT, "tests", "integration",
                     "test_fake_analyzer_execution.py"),
        os.path.join(PROJECT_ROOT, "tests", "integration",
                     "test_real_analyzer_runner.py"),
    ]
    for f in files:
        src = open(f, encoding="utf-8").read()
        dups = _find_duplicate_test_names(src)
        assert dups == [], f"{os.path.basename(f)} 有重複測試：{dups}"


def test_duplicate_detector_positive_control():
    source = "def test_same(): pass\ndef test_same(): pass\n"
    assert _find_duplicate_test_names(source) == ["test_same"]


def test_duplicate_detector_allow_control():
    source = "def test_a(): pass\ndef test_b(): pass\n"
    assert _find_duplicate_test_names(source) == []


# ================================================================
# Phase 6.4C2-B1.4 — Non-string run-ID / unique fixed errors
# ================================================================
def _assert_safe_blocked(s, runs, bad_value):
    assert s.status == "blocked"
    assert re.fullmatch(r"run-[0-9a-f]{12}", s.run_id), "安全 trace ID"
    assert "execution_plan_invalid" in s.fixed_error_codes
    raw = json.dumps(s.to_dict())
    assert str(bad_value) not in raw, "原始值不得暴露"
    dirs = os.listdir(runs)
    assert len(dirs) == 1 and dirs[0] == s.run_id
    a = json.load(open(os.path.join(runs, dirs[0], "audit.json"),
                       encoding="utf-8"))
    assert validate_audit_manifest(a) == [], "audit 必須合法"
    assert str(bad_value) not in json.dumps(a)
    assert "audit_write_failed" not in a["fixed_error_codes"]


def _bad_run_id_execute(value):
    case = _case(case_id="case-NRI")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = value
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    return s, runs


def test_none_plan_run_id_safely_blocked():
    s, runs = _bad_run_id_execute(None)
    _assert_safe_blocked(s, runs, None)


def test_integer_plan_run_id_safely_blocked():
    s, runs = _bad_run_id_execute(12345)
    _assert_safe_blocked(s, runs, 12345)


def test_object_plan_run_id_safely_blocked():
    s, runs = _bad_run_id_execute({"run": "id"})
    _assert_safe_blocked(s, runs, {"run": "id"})


def test_non_string_run_id_does_not_raise():
    # 直接執行不得 raise TypeError（None/int/object 都安全）
    for value in (None, 12345, {"run": "id"}):
        s, runs = _bad_run_id_execute(value)
        assert s.status == "blocked"


def test_non_string_run_id_not_exposed():
    s, runs = _bad_run_id_execute(12345)
    assert "12345" not in s.run_id
    assert "12345" not in json.dumps(s.to_dict())


def test_non_string_run_id_writes_safe_blocked_audit():
    s, runs = _bad_run_id_execute(None)
    a = json.load(open(os.path.join(runs, s.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["run_id"] == s.run_id
    assert "None" not in json.dumps(a)


def test_non_string_run_id_does_not_add_audit_write_failed():
    s, runs = _bad_run_id_execute(object())
    assert "audit_write_failed" not in s.fixed_error_codes
    a = json.load(open(os.path.join(runs, s.run_id, "audit.json"),
                       encoding="utf-8"))
    assert "audit_write_failed" not in a["fixed_error_codes"]


# ---- fixed_error_codes 去重 ----
def test_invalid_run_id_reports_execution_plan_invalid_once():
    case = _case(case_id="case-DUP")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "bad-id"
    plan.cache_namespace = "namespace-bad"  # 第二個 invalid 來源
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert s.fixed_error_codes.count("execution_plan_invalid") == 1, \
        f"重複出現：{s.fixed_error_codes}"
    a = json.load(open(os.path.join(runs, s.run_id, "audit.json"),
                       encoding="utf-8"))
    assert a["fixed_error_codes"].count("execution_plan_invalid") == 1


def test_preflight_fixed_error_codes_are_unique():
    # 多種 invalid 同時存在（bad run_id + bad created_at + bad ns）
    case = _case(case_id="case-UNIQ")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "bad-id"
    plan.created_at = "not-a-date"
    plan.cache_namespace = "namespace-zzzzzzzzzzzz"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    assert len(s.fixed_error_codes) == len(set(s.fixed_error_codes)), \
        "summary fixed_error_codes 不得重複"


def test_blocked_audit_fixed_error_codes_are_unique():
    case = _case(case_id="case-UNIQ2")
    plan = build_execution_plan(
        eligible_cases=[case.as_dict()], run_salt="salt-1",
        adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    plan.run_id = "bad-id"
    root, cache, runs = _env()
    s = execute_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=_loader(),
        adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
        audit_dir=runs, allowed_root=root,
        analyzer_name=FAKE_ANALYZER_NAME,
        analyzer_version=FAKE_ANALYZER_VERSION)
    a = json.load(open(os.path.join(runs, s.run_id, "audit.json"),
                       encoding="utf-8"))
    codes = a["fixed_error_codes"]
    assert len(codes) == len(set(codes)), "audit fixed_error_codes 不得重複"
    for c in codes:
        assert c in KNOWN_ERROR_CODES  # 從 audit 模組 import


def test_fixed_error_order_is_deterministic():
    # 相同 invalid plan 兩次執行 → 錯誤碼順序一致
    def run_once():
        case = _case(case_id="case-ORD2")
        plan = build_execution_plan(
            eligible_cases=[case.as_dict()], run_salt="salt-1",
            adapter_name=FAKE_ANALYZER_NAME, dry_run=False, authorized=True,
            created_at="2026-08-01T00:00:00Z")
        plan.run_id = "bad-id"
        plan.created_at = "not-a-date"
        root, cache, runs = _env()
        s = execute_external_analyzer_plan(
            plan=plan, eligible_cases=[case], loader=_loader(),
            adapter=FakeExternalAnalyzerAdapter(), cache_dir=cache,
            audit_dir=runs, allowed_root=root,
            analyzer_name=FAKE_ANALYZER_NAME,
            analyzer_version=FAKE_ANALYZER_VERSION)
        return s.fixed_error_codes
    assert run_once() == run_once(), "錯誤碼順序必須確定"
