# -*- coding: utf-8 -*-
"""test_analyzer_cache.py — Cache + audit + fake adapter（Phase 6.4C2-B0）"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from alkaid_cs2.evaluation.analyzer_audit import (  # noqa: E402
    ALLOWED_AUDIT_FIELDS,
    AnalyzerAuditWriteError,
    hash_image_hashes,
    write_audit_manifest,
)
from alkaid_cs2.evaluation.analyzer_cache import (  # noqa: E402
    ALLOWED_CACHE_FIELDS,
    AnalyzerCacheWriteError,
    build_cache_record,
    compute_opaque_case_key,
    validate_normalized_result,
    write_analyzer_cache_record,
)
from alkaid_cs2.evaluation.external_analyzer_adapter import (  # noqa: E402
    FakeExternalAnalyzerAdapter,
    FailingExternalAnalyzerAdapter,
)

VALID_RESULT = {
    "kind": "image",
    "item_count": 1,
    "items": [{"name": "AK-47 | Redline", "wear": "Field-Tested",
               "currency": "TWD", "price": "5000"}],
}


def _record(**over):
    r = build_cache_record(
        opaque_case_key="a" * 64, image_index=0, image_sha256="b" * 64,
        analyzer_name="fake-analyzer", analyzer_version="0.1.0",
        analyzed_at="2026-08-01T00:00:00Z", normalized_result=VALID_RESULT)
    r.update(over)
    return r


def test_cache_record_schema_valid(tmp_path):
    r = _record()
    path = write_analyzer_cache_record(r, tmp_path)
    assert os.path.exists(path)
    data = json.load(open(path, encoding="utf-8"))
    assert data["schema_version"] == "analyzer-cache-v1"
    assert data["status"] == "success"
    assert len(data["result_sha256"]) == 64


def test_cache_does_not_contain_bytes(tmp_path):
    r = _record()
    path = write_analyzer_cache_record(r, tmp_path)
    raw = open(path, encoding="utf-8").read()
    assert "image_bytes" not in raw and "base64" not in raw


def test_cache_does_not_contain_storage_reference(tmp_path):
    r = _record()
    path = write_analyzer_cache_record(r, tmp_path)
    raw = open(path, encoding="utf-8").read()
    assert "secure-store://" not in raw
    assert "storage_reference" not in raw


def test_cache_does_not_contain_case_id(tmp_path):
    r = _record()
    path = write_analyzer_cache_record(r, tmp_path)
    raw = open(path, encoding="utf-8").read()
    assert "case_id" not in raw


def test_invalid_normalized_result_rejected(tmp_path):
    r = _record()
    r["normalized_result"] = {"kind": "image"}  # 缺 item_count/items
    with pytest.raises(AnalyzerCacheWriteError, match="cache_record_invalid"):
        write_analyzer_cache_record(r, tmp_path)


def test_cache_write_failure_exit_2(tmp_path, monkeypatch):
    import os as _os
    r = _record()

    def _boom(src, dst):
        raise OSError("simulated")
    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(AnalyzerCacheWriteError, match="cache_write_failed"):
        write_analyzer_cache_record(r, tmp_path)


def test_opaque_key_irreversible():
    k = compute_opaque_case_key("real_001", "salt")
    assert len(k) == 64 and k.isalnum()
    assert "real_001" not in k


def test_fake_adapter_deterministic():
    a = FakeExternalAnalyzerAdapter()
    img = b"\x89PNG fake"
    r1 = a.analyze_image(img, case_key="k", image_index=0)
    r2 = a.analyze_image(img, case_key="k", image_index=0)
    assert r1 == r2, "fake adapter 必須 deterministic"
    assert r1["item_count"] == 1


def test_failing_adapter_returns_fixed_error():
    a = FailingExternalAnalyzerAdapter()
    with pytest.raises(RuntimeError):
        a.analyze_image(b"x", case_key="k", image_index=0)


def test_production_sdk_not_imported():
    import re
    import alkaid_cs2.evaluation.external_analyzer_adapter as mod
    src = open(mod.__file__, encoding="utf-8").read()
    # 只檢查實際 import 陳述（docstring 提及不算）
    for banned in ("requests", "urllib", "socket", "openai", "anthropic",
                   "deepseek", "httpx", "aiohttp"):
        assert not re.search(
            rf"^\s*(?:import|from)\s+{banned}\b", src,
            re.MULTILINE), f"不得 import {banned}"


def test_network_modules_not_imported():
    import alkaid_cs2.evaluation.secure_image_loader as mod
    src = open(mod.__file__, encoding="utf-8").read()
    for banned in ("requests", "urllib", "socket", "httpx", "aiohttp"):
        assert banned not in src


def test_production_secrets_not_read():
    import alkaid_cs2.evaluation.external_analyzer_adapter as mod
    import alkaid_cs2.evaluation.external_analyzer_runner as runner
    for m in (mod, runner):
        src = open(m.__file__, encoding="utf-8").read()
        for secret in ("os.environ", "getenv", "API_KEY", "FB_COOKIE",
                       "config.txt"):
            assert secret not in src, f"{m.__file__} 不得讀 secrets"


def test_audit_manifest_contains_only_allowed_fields(tmp_path):
    audit = {
        "schema_version": "analyzer-audit-v1", "run_id": "run-" + "a" * 12,
        "started_at": "2026-08-01T00:00:00Z",
                 "completed_at": "2026-08-01T00:00:01Z", "dry_run": True,
        "authorization_flag_present": False, "authorization_env_present": False,
        "eligible_case_count": 0, "eligible_image_count": 0,
        "attempted_image_count": 0, "succeeded_image_count": 0,
        "failed_image_count": 0, "cache_write_count": 0,
        "result": "blocked", "fixed_error_codes": ["no_eligible_real_cases"],
        "image_hash_hashes": [],
    }
    path = write_audit_manifest(audit, tmp_path)
    data = json.load(open(path, encoding="utf-8"))
    assert set(data) <= ALLOWED_AUDIT_FIELDS


def test_audit_manifest_no_storage_reference(tmp_path):
    audit = dict(schema_version="analyzer-audit-v1", run_id="run-" + "a" * 12,
                 started_at="2026-08-01T00:00:00Z", completed_at="2026-08-01T00:00:01Z", dry_run=True,
                 authorization_flag_present=False,
                 authorization_env_present=False, eligible_case_count=0,
                 eligible_image_count=0, attempted_image_count=0,
                 succeeded_image_count=0, failed_image_count=0,
                 cache_write_count=0, result="blocked",
                 fixed_error_codes=[], image_hash_hashes=[])
    path = write_audit_manifest(audit, tmp_path)
    raw = open(path, encoding="utf-8").read()
    assert "secure-store://" not in raw


def test_audit_manifest_no_case_id(tmp_path):
    audit = dict(schema_version="analyzer-audit-v1", run_id="run-" + "a" * 12,
                 started_at="2026-08-01T00:00:00Z", completed_at="2026-08-01T00:00:01Z", dry_run=True,
                 authorization_flag_present=False,
                 authorization_env_present=False, eligible_case_count=0,
                 eligible_image_count=0, attempted_image_count=0,
                 succeeded_image_count=0, failed_image_count=0,
                 cache_write_count=0, result="blocked",
                 fixed_error_codes=[], image_hash_hashes=[])
    path = write_audit_manifest(audit, tmp_path)
    raw = open(path, encoding="utf-8").read()
    assert "case_id" not in raw


def test_audit_write_failure_controlled(tmp_path, monkeypatch):
    import os as _os
    audit = dict(schema_version="analyzer-audit-v1",
                 run_id="run-" + "a" * 12,
                 started_at="2026-08-01T00:00:00Z",
                 completed_at="2026-08-01T00:00:01Z", dry_run=True,
                 authorization_flag_present=False,
                 authorization_env_present=False, eligible_case_count=0,
                 eligible_image_count=0, attempted_image_count=0,
                 succeeded_image_count=0, failed_image_count=0,
                 cache_write_count=0, result="blocked",
                 fixed_error_codes=[], image_hash_hashes=[])

    def _boom(src, dst):
        raise OSError("simulated")
    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(AnalyzerAuditWriteError, match="audit_write_failed"):
        write_audit_manifest(audit, tmp_path)


def test_hash_image_hashes_not_reversible():
    out = hash_image_hashes(["a" * 64])
    assert out[0] != "a" * 64
    assert len(out[0]) == 64


# ================================================================
# Phase 6.4C2-B0.1 — Strict schema / atomic write
# ================================================================
from alkaid_cs2.evaluation.analyzer_audit import validate_audit_manifest  # noqa: E402
from alkaid_cs2.evaluation.analyzer_cache import validate_cache_record  # noqa: E402


def _full_audit(**over):
    a = dict(schema_version="analyzer-audit-v1",
             run_id="run-" + "a" * 12,
             started_at="2026-08-01T00:00:00Z",
             completed_at="2026-08-01T00:00:01Z", dry_run=True,
             authorization_flag_present=False,
             authorization_env_present=False, eligible_case_count=0,
             eligible_image_count=0, attempted_image_count=0,
             succeeded_image_count=0, failed_image_count=0,
             cache_write_count=0, result="blocked",
             fixed_error_codes=[], image_hash_hashes=[])
    a.update(over)
    return a


def test_normalized_result_unknown_field_rejected():
    r = dict(VALID_RESULT, extra_field="x")
    assert any("unknown_fields" in e for e in
               validate_normalized_result(r))


def test_normalized_result_nested_token_rejected():
    r = dict(VALID_RESULT, warnings=["token sk-abc"])
    assert any("privacy:auth_keyword" in e for e in
               validate_normalized_result(r))


def test_normalized_result_storage_reference_rejected():
    r = dict(VALID_RESULT, kind="image",
             items=[{"name": "secure-store://secret-x"}])
    assert any("privacy" in e or "unknown_fields" in e for e in
               validate_normalized_result(r))


def test_normalized_result_bytes_rejected():
    r = dict(VALID_RESULT)
    r["items"] = [{"name": "x", "price": b"bytes-here"}]
    assert any("price_invalid_type" in e for e in
               validate_normalized_result(r))


def test_normalized_result_item_count_mismatch_rejected():
    r = dict(VALID_RESULT, item_count=2)
    assert any("item_count_mismatch" in e for e in
               validate_normalized_result(r))


def test_normalized_item_unknown_field_rejected():
    r = dict(VALID_RESULT)
    r["items"] = [{"name": "x", "wear": "Field-Tested", "secret": "v"}]
    assert any("unknown_fields" in e for e in
               validate_normalized_result(r))


def test_fake_adapter_result_schema_valid():
    a = FakeExternalAnalyzerAdapter()
    result = a.analyze_image(b"\x89PNG", case_key="k", image_index=0)
    assert validate_normalized_result(result) == []


def test_cache_result_hash_mismatch_rejected():
    r = _record()
    r["result_sha256"] = "f" * 64  # 與 normalized_result 不符
    assert any("result_sha256_mismatch" in e for e in
               validate_cache_record(r))


def test_cache_status_invalid_rejected():
    r = _record(status="whatever")
    assert any("status_invalid" in e for e in validate_cache_record(r))


def test_cache_timestamp_invalid_rejected():
    r = _record(analyzed_at="2026-99-99T25:00:00Z")
    assert any("analyzed_at_invalid" in e for e in validate_cache_record(r))


def test_cache_empty_analyzer_name_rejected():
    r = _record(analyzer_name="   ")
    assert any("analyzer_name_empty" in e for e in validate_cache_record(r))


def test_cache_record_nested_sensitive_rejected():
    r = _record()
    r["normalized_result"] = dict(VALID_RESULT,
                                  warnings=["電話 0912345678"])
    assert any("privacy" in e for e in validate_cache_record(r))


def test_audit_invalid_count_rejected():
    a = _full_audit(eligible_case_count=True)
    assert any("eligible_case_count_invalid" in e for e in
               validate_audit_manifest(a))
    a2 = _full_audit(succeeded_image_count=5, attempted_image_count=3)
    assert any("count_sum_exceeds_attempted" in e for e in
               validate_audit_manifest(a2))


def test_audit_invalid_bool_rejected():
    a = _full_audit(dry_run="yes")
    assert any("dry_run_invalid" in e for e in validate_audit_manifest(a))


def test_audit_unknown_error_code_rejected():
    a = _full_audit(fixed_error_codes=["made_up_code_xyz"])
    assert any("unknown_error_code" in e for e in
               validate_audit_manifest(a))


def test_audit_invalid_hash_hash_rejected():
    a = _full_audit(image_hash_hashes=["not-a-hash"])
    assert any("image_hash_hashes_invalid" in e for e in
               validate_audit_manifest(a))


def test_audit_sensitive_value_rejected():
    a = _full_audit(run_id="run-" + "a" * 12)
    a["fixed_error_codes"] = []
    a["image_hash_hashes"] = []
    # 塞敏感值到未知欄位 → unknown_fields（且 privacy scan 拒）
    a["notes"] = "聯絡 aaa@bbb.com"
    errs = validate_audit_manifest(a)
    assert any("unknown_fields" in e or "privacy" in e for e in errs)


def test_audit_invalid_timestamp_rejected():
    a = _full_audit(started_at="2026-08-01T25:00:00Z")
    assert any("started_at_invalid" in e for e in
               validate_audit_manifest(a))


# ---- Atomic single-file write ----
def test_cache_partial_write_leaves_no_target(tmp_path, monkeypatch):
    import os as _os
    r = _record()
    real_open = open
    calls = {"n": 0}

    def flaky_open(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:  # temp 寫入失敗（唯一 open）
            raise OSError("simulated")
        return real_open(*a, **k)
    monkeypatch.setattr("builtins.open", flaky_open)
    with pytest.raises(AnalyzerCacheWriteError):
        write_analyzer_cache_record(r, tmp_path)
    assert list(tmp_path.iterdir()) == [], "不得留下 target 或 temp"


def test_cache_replace_failure_cleans_temp(tmp_path, monkeypatch):
    import os as _os
    r = _record()

    def _boom(src, dst):
        raise OSError("simulated")
    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(AnalyzerCacheWriteError):
        write_analyzer_cache_record(r, tmp_path)
    assert list(tmp_path.iterdir()) == [], "replace 失敗後 temp 必須清理"


def test_audit_partial_write_leaves_no_target(tmp_path, monkeypatch):
    import os as _os
    a = _full_audit()
    real_open = open
    calls = {"n": 0}

    def flaky_open(*a2, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("simulated")
        return real_open(*a2, **k)
    monkeypatch.setattr("builtins.open", flaky_open)
    with pytest.raises(AnalyzerAuditWriteError):
        write_audit_manifest(a, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_audit_replace_failure_cleans_temp(tmp_path, monkeypatch):
    import os as _os
    a = _full_audit()

    def _boom(src, dst):
        raise OSError("simulated")
    monkeypatch.setattr(_os, "replace", _boom)
    with pytest.raises(AnalyzerAuditWriteError):
        write_audit_manifest(a, tmp_path)
    assert list(tmp_path.iterdir()) == []


def test_successful_atomic_cache_valid_json(tmp_path):
    r = _record()
    path = write_analyzer_cache_record(r, tmp_path)
    data = json.load(open(path, encoding="utf-8"))
    assert validate_cache_record(data) == []


def test_successful_atomic_audit_valid_json(tmp_path):
    a = _full_audit()
    path = write_audit_manifest(a, tmp_path)
    data = json.load(open(path, encoding="utf-8"))
    assert validate_audit_manifest(data) == []


# ================================================================
# Phase 6.4C2-B0.1 — Controlled SHA-256 base64 exemption scope
# ================================================================
def _scan_errs(payload):
    from alkaid_cs2.evaluation.intake_validation import scan_redaction_issues
    return [f for f in scan_redaction_issues(payload) if f.severity == "error"]


def test_controlled_sha256_field_not_base64_false_positive():
    # 純 64 位小寫 hex 不是 base64（受控 hash 欄位值）
    h = "ab" * 32
    assert not any(f.code == "base64_like"
                   for f in _scan_errs({"image_sha256": h}))
    assert not any(f.code == "base64_like"
                   for f in _scan_errs({"result_sha256": h}))
    assert not any(f.code == "base64_like"
                   for f in _scan_errs({"image_hash_hashes": [h]}))


def test_arbitrary_field_64_char_string_still_scanned():
    # 任意欄位的 64 字元字串若含 base64 特徵仍掃描（如含 + / 或大寫）
    b64 = ("A" * 40) + "B+/="
    fs = _scan_errs({"notes": b64})
    assert any(f.code == "base64_like" for f in fs), \
        "任意欄位的 base64-like 不得豁免"


def test_notes_long_token_like_string_rejected():
    fs = _scan_errs({"notes": "token sk-abcdefghijklmnopqrstuvwxyz123456"})
    assert any(f.code in ("auth_keyword", "base64_like") for f in fs)


def test_nested_metadata_base64_like_rejected():
    fs = _scan_errs({"metadata": {"description": "Q" * 40 + "+/="}})
    assert any(f.code == "base64_like" for f in fs), "nested 仍掃描"

# ================================================================
# Phase 6.4C2-B0.2 — Field-scoped SHA-256 exemption + warnings schema
# ================================================================
HEX64 = "ab" * 32


def _scan2(payload):
    from alkaid_cs2.evaluation.intake_validation import scan_redaction_issues
    return [f for f in scan_redaction_issues(payload) if f.severity == "error"]


def test_controlled_hash_field_64_hex_accepted():
    assert not any(f.code == "base64_like"
                   for f in _scan2({"image_sha256": HEX64}))
    assert not any(f.code == "base64_like"
                   for f in _scan2({"result_sha256": HEX64}))


def test_arbitrary_field_pure_64_hex_still_scanned():
    # 非受控欄位（notes）不得享受 SHA256 豁免——helper 直接驗證
    from alkaid_cs2.evaluation.intake_validation import (
        _is_controlled_sha256_field,
    )
    assert not _is_controlled_sha256_field("notes", HEX64)
    assert not _is_controlled_sha256_field("arbitrary_field", HEX64)
    # 受控欄位才豁免
    assert _is_controlled_sha256_field("image_sha256", HEX64)
    # 非受控欄位的 base64-like 值照常觸發
    b64ish = "A" * 40 + "+/="
    fs2 = _scan2({"notes": b64ish})
    assert any(f.code == "base64_like" for f in fs2)


def test_notes_pure_64_hex_still_scanned():
    # notes 內放「token」+ 64hex → auth_keyword 觸發（自由文字無豁免）
    fs = _scan2({"notes": f"token {HEX64}"})
    assert any(f.code == "auth_keyword" for f in fs)


def test_nested_metadata_pure_64_hex_still_scanned():
    b64ish = "A" * 40 + "+/="
    fs = _scan2({"metadata": {"description": b64ish}})
    assert any(f.code == "base64_like" for f in fs)


def test_hash_named_unknown_field_not_exempt():
    # 欄位名像 hash 但不在 allowlist（如 my_image_sha256）→ 不豁免
    b64ish = "A" * 40 + "+/="
    fs = _scan2({"my_image_sha256": b64ish})
    assert any(f.code == "base64_like" for f in fs)


def test_controlled_hash_list_accepted():
    fs = _scan2({"image_hash_hashes": [HEX64, "cd" * 32]})
    assert not any(f.code == "base64_like" for f in fs)


def test_arbitrary_list_of_64_hex_still_scanned():
    # 任意欄位的 list 元素 → 元素走 field[0] path → 欄位名非受控 → 照常掃描
    b64ish = "A" * 40 + "+/="
    fs = _scan2({"notes": [b64ish]})
    assert any(f.code == "base64_like" for f in fs)


# ---- Warnings schema ----
def test_warnings_non_string_rejected():
    r = dict(VALID_RESULT, warnings=[123])
    assert any("warnings_0_invalid" in e for e in
               validate_normalized_result(r))


def test_warnings_empty_string_rejected():
    r = dict(VALID_RESULT, warnings=[""])
    assert any("warnings_0_invalid" in e for e in
               validate_normalized_result(r))


def test_warnings_too_long_rejected():
    r = dict(VALID_RESULT, warnings=["x" * 201])
    assert any("warnings_0_too_long" in e for e in
               validate_normalized_result(r))


def test_warnings_too_many_rejected():
    r = dict(VALID_RESULT, warnings=["w"] * 51)
    assert any("warnings_too_many" in e for e in
               validate_normalized_result(r))


def test_warnings_sensitive_string_rejected():
    r = dict(VALID_RESULT, warnings=["電話 0912345678"])
    assert any("privacy" in e for e in validate_normalized_result(r))


def test_valid_warnings_accepted():
    r = dict(VALID_RESULT, warnings=["低解析度", "多物品"])
    assert validate_normalized_result(r) == []
