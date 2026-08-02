# -*- coding: utf-8 -*-
"""test_analyzer_audit_v3.py — Phase 6.4C2-B2-A Audit v3 schema"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.analyzer_audit import (  # noqa: E402
    AUDIT_SCHEMA_VERSION,
    AUDIT_SCHEMA_VERSION_V2,
    AUDIT_SCHEMA_VERSION_V3,
    validate_audit_manifest,
)

SHA256 = "b" * 64


def _v3(**over):
    a = {
        "schema_version": AUDIT_SCHEMA_VERSION_V3,
        "run_id": "run-" + "a" * 12,
        "started_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:00:01Z",
        "dry_run": False,
        "authorization_flag_present": True,
        "authorization_env_present": True,
        "authorization_env_accepted": True,
        "authorization_context_present": True,
        "authorization_context_valid": True,
        "authorization_decision": True,
        "authorization_context_digest": SHA256,
        "network_policy_version": "deny-all-1",
        "eligible_case_count": 1,
        "eligible_image_count": 1,
        "processed_image_count": 1,
        "attempted_image_count": 1,
        "succeeded_image_count": 1,
        "failed_image_count": 0,
        "cache_hit_count": 0,
        "cache_miss_count": 1,
        "cache_invalid_count": 0,
        "cache_write_count": 1,
        "requested_network_call_count": 0,
        "allowed_network_call_count": 0,
        "result": "completed",
        "fixed_error_codes": [],
        "image_hash_hashes": ["a" * 64],
        "analyzer_name": "fake-analyzer",
        "analyzer_version": "0.1.0",
    }
    a.update(over)
    return a


def test_valid_v3_accepted():
    assert validate_audit_manifest(_v3()) == []


def test_unknown_field_rejected():
    errs = validate_audit_manifest(_v3(extra_field=1))
    assert any("unknown_fields" in e for e in errs)


def test_invalid_digest_rejected():
    errs = validate_audit_manifest(_v3(authorization_context_digest="short"))
    assert "authorization_context_digest_invalid" in errs


def test_decision_non_bool_rejected():
    errs = validate_audit_manifest(_v3(authorization_decision=1))
    assert "authorization_decision_invalid" in errs


def test_requested_network_calls_nonzero_rejected():
    errs = validate_audit_manifest(_v3(requested_network_call_count=1))
    assert "requested_network_call_count_nonzero" in errs


def test_allowed_network_calls_nonzero_rejected():
    errs = validate_audit_manifest(_v3(allowed_network_call_count=2))
    assert "allowed_network_call_count_nonzero" in errs


def test_fixed_unknown_error_rejected():
    errs = validate_audit_manifest(_v3(fixed_error_codes=["mystery_code"]))
    assert any("unknown_error_code" in e for e in errs)


def test_sentinel_privacy_leakage_rejected():
    errs = validate_audit_manifest(_v3(token="sk-secret-123"))
    assert any("unknown_fields" in e for e in errs)
    assert any("privacy" in e for e in errs)


def test_valid_v1_remains_valid():
    a = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "run_id": "run-" + "a" * 12,
        "started_at": "2026-08-01T00:00:00Z",
        "completed_at": "2026-08-01T00:00:01Z",
        "dry_run": True,
        "authorization_flag_present": False,
        "authorization_env_present": False,
        "eligible_case_count": 0,
        "eligible_image_count": 0,
        "attempted_image_count": 0,
        "succeeded_image_count": 0,
        "failed_image_count": 0,
        "cache_write_count": 0,
        "result": "blocked",
        "fixed_error_codes": ["no_eligible_real_cases"],
        "image_hash_hashes": [],
    }
    assert validate_audit_manifest(a) == []


def test_valid_v2_remains_valid():
    a = _v3(schema_version=AUDIT_SCHEMA_VERSION_V2)
    del a["authorization_env_accepted"]
    del a["authorization_context_present"]
    del a["authorization_context_valid"]
    del a["authorization_decision"]
    del a["authorization_context_digest"]
    del a["network_policy_version"]
    del a["requested_network_call_count"]
    del a["allowed_network_call_count"]
    assert validate_audit_manifest(a) == []


def test_existing_invalid_v1_remains_invalid():
    a = {
        "schema_version": AUDIT_SCHEMA_VERSION,
        "run_id": "bad-run",
        "started_at": "not-a-time",
        "completed_at": "not-a-time",
        "dry_run": "yes",
        "result": "mystery",
        "fixed_error_codes": ["unknown_code"],
        "image_hash_hashes": ["not-hash"],
    }
    assert validate_audit_manifest(a) != []


def test_existing_invalid_v2_remains_invalid():
    a = _v3(schema_version=AUDIT_SCHEMA_VERSION_V2)
    del a["authorization_env_accepted"]
    del a["authorization_context_present"]
    del a["authorization_context_valid"]
    del a["authorization_decision"]
    del a["authorization_context_digest"]
    del a["network_policy_version"]
    del a["requested_network_call_count"]
    del a["allowed_network_call_count"]
    a["attempted_image_count"] = 99  # attempted > misses → invalid
    assert validate_audit_manifest(a) != []


# ================================================================
# Phase 6.4C2-B2-A.1 — Audit v3 state/digest relationships
# ================================================================
def test_context_absent_digest_empty_decision_false_accepted():
    a = _v3(authorization_context_present=False,
            authorization_context_valid=False,
            authorization_decision=False,
            authorization_context_digest="")
    assert validate_audit_manifest(a) == []


def test_context_absent_64hex_digest_rejected():
    a = _v3(authorization_context_present=False,
            authorization_context_valid=False,
            authorization_decision=False)
    errs = validate_audit_manifest(a)
    assert "authorization_context_digest_invalid" in errs


def test_context_present_empty_digest_rejected():
    a = _v3(authorization_context_digest="")
    errs = validate_audit_manifest(a)
    assert "authorization_context_digest_invalid" in errs


def test_context_valid_true_present_false_rejected():
    a = _v3(authorization_context_present=False,
            authorization_context_valid=True,
            authorization_decision=False,
            authorization_context_digest="")
    errs = validate_audit_manifest(a)
    assert "authorization_context_state_invalid" in errs


def test_decision_true_flag_false_rejected():
    a = _v3(authorization_flag_present=False)
    errs = validate_audit_manifest(a)
    assert "authorization_decision_state_invalid" in errs


def test_decision_true_env_present_false_rejected():
    a = _v3(authorization_env_present=False)
    errs = validate_audit_manifest(a)
    assert "authorization_decision_state_invalid" in errs


def test_decision_true_env_accepted_false_rejected():
    a = _v3(authorization_env_accepted=False)
    errs = validate_audit_manifest(a)
    assert "authorization_decision_state_invalid" in errs


def test_decision_true_context_present_false_rejected():
    a = _v3(authorization_context_present=False,
            authorization_context_valid=False,
            authorization_decision=True,
            authorization_context_digest="")
    errs = validate_audit_manifest(a)
    assert "authorization_decision_state_invalid" in errs


def test_decision_true_context_valid_false_rejected():
    a = _v3(authorization_context_valid=False)
    errs = validate_audit_manifest(a)
    assert "authorization_decision_state_invalid" in errs


# ---- B2-A.1 固定碼進 KNOWN_ERROR_CODES ----
def test_authorization_flag_missing_accepted_as_fixed_error():
    a = _v3(fixed_error_codes=["authorization_flag_missing"])
    errs = validate_audit_manifest(a)
    assert "unknown_error_code" not in " ".join(errs), errs


def test_authorization_binding_repository_mismatch_accepted():
    a = _v3(fixed_error_codes=[
        "authorization_binding_repository_mismatch"])
    errs = validate_audit_manifest(a)
    assert "unknown_error_code" not in " ".join(errs), errs


def test_authorization_requested_budget_invalid_accepted():
    a = _v3(fixed_error_codes=["authorization_requested_budget_invalid"])
    errs = validate_audit_manifest(a)
    assert "unknown_error_code" not in " ".join(errs), errs


def test_network_policy_not_deny_all_accepted():
    a = _v3(fixed_error_codes=["network_policy_not_deny_all"])
    errs = validate_audit_manifest(a)
    assert "unknown_error_code" not in " ".join(errs), errs


def test_unknown_fixed_error_rejected():
    a = _v3(fixed_error_codes=["mystery_code_xyz"])
    errs = validate_audit_manifest(a)
    assert any("unknown_error_code" in e for e in errs)


# ================================================================
# Phase 6.4C2-B2-A.3 — Fixed-error union / no fail-open / no f-string codes
# ================================================================
def test_authorization_codes_subset_of_known_error_codes():
    from alkaid_cs2.evaluation.authorization_context import (
        AUTHORIZATION_ALL_ERROR_CODES,
    )
    from alkaid_cs2.evaluation.analyzer_audit import KNOWN_ERROR_CODES
    assert AUTHORIZATION_ALL_ERROR_CODES <= KNOWN_ERROR_CODES, \
        AUTHORIZATION_ALL_ERROR_CODES - KNOWN_ERROR_CODES


def test_network_policy_codes_subset_of_known_error_codes():
    from alkaid_cs2.evaluation.network_policy import (
        NETWORK_POLICY_ERROR_CODES,
    )
    from alkaid_cs2.evaluation.analyzer_audit import KNOWN_ERROR_CODES
    assert NETWORK_POLICY_ERROR_CODES <= KNOWN_ERROR_CODES, \
        NETWORK_POLICY_ERROR_CODES - KNOWN_ERROR_CODES


def test_module_import_order_independent():
    # 先 import analyzer_audit 再 import authorization/network_policy
    # （與檔案內 import 順序無關，無 circular import）
    import importlib
    for name in ("alkaid_cs2.evaluation.analyzer_audit",
                 "alkaid_cs2.evaluation.authorization_context",
                 "alkaid_cs2.evaluation.network_policy"):
        importlib.import_module(name)
    import alkaid_cs2.evaluation.analyzer_audit as aa
    import alkaid_cs2.evaluation.authorization_context as ac
    import alkaid_cs2.evaluation.network_policy as np
    assert ac.AUTHORIZATION_ALL_ERROR_CODES <= aa.KNOWN_ERROR_CODES
    assert np.NETWORK_POLICY_ERROR_CODES <= aa.KNOWN_ERROR_CODES


def test_no_import_error_fail_open_in_audit():
    src = open(os.path.join(
        PROJECT_ROOT, "alkaid_cs2", "evaluation", "analyzer_audit.py"),
        encoding="utf-8").read()
    assert "except ImportError" not in src, "不得有 fail-open"
    assert "from alkaid_cs2.evaluation.authorization_context import" in src
    assert "from alkaid_cs2.evaluation.network_policy import" in src


def test_no_dynamic_authorization_binding_codes():
    # AST/source proof：authorization_context.py 不得用 f-string 組 error code
    src = open(os.path.join(
        PROJECT_ROOT, "alkaid_cs2", "evaluation",
        "authorization_context.py"), encoding="utf-8").read()
    assert 'f"authorization_binding_' not in src
    assert "f'authorization_binding_" not in src
    assert "ERROR_BINDING_REPOSITORY_MISMATCH" in src
    assert "ERROR_BINDING_NETWORK_POLICY_VERSION_MISMATCH" in src
