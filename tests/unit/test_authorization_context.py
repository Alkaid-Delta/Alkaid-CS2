# -*- coding: utf-8 -*-
"""test_authorization_context.py — Phase 6.4C2-B2-A Authorization"""
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from alkaid_cs2.evaluation.authorization_context import (  # noqa: E402
    AUTHORIZATION_SCHEMA_VERSION,
    AuthorizationContextV1,
    AuthorizationDecision,
    compute_authorization_context_digest,
    evaluate_authorization,
    validate_authorization_context,
)

NOW = "2026-08-01T12:00:00Z"
SHA1 = "a" * 40
SHA256 = "b" * 64
RUN_ID = "run-" + "c" * 12
AUTH_ID = "auth-" + "d" * 16


def _valid_context(**over):
    ctx = dict(
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        authorization_id=AUTH_ID,
        authorization_scope="evaluation",
        approved_at="2026-08-01T00:00:00Z",
        expires_at="2026-08-02T00:00:00Z",
        repository="Alkaid-Delta/Alkaid-CS2",
        branch="agent/v2-vision-real-evaluation",
        commit_sha=SHA1,
        dataset_manifest_sha256=SHA256,
        execution_mode="contract_only",
        approved_run_id=RUN_ID,
        loader_name="in-memory-loader",
        loader_version="1.0.0",
        adapter_name="fake-analyzer",
        adapter_version="0.1.0",
        adapter_config_sha256=SHA256,
        network_policy_version="deny-all-1",
        max_case_count=10,
        max_image_count=20,
        max_network_calls=0,
        max_total_image_bytes=1_000_000,
        max_wall_time_seconds=300,
    )
    ctx.update(over)
    return AuthorizationContextV1(**ctx)


def _valid_kwargs(**over):
    kw = dict(
        authorization_flag_present=True,
        authorization_env_present=True,
        authorization_env_accepted=True,
        authorization_context=_valid_context(),
        expected_repository="Alkaid-Delta/Alkaid-CS2",
        expected_branch="agent/v2-vision-real-evaluation",
        expected_commit_sha=SHA1,
        expected_manifest_sha256=SHA256,
        expected_run_id=RUN_ID,
        expected_loader_name="in-memory-loader",
        expected_loader_version="1.0.0",
        expected_adapter_name="fake-analyzer",
        expected_adapter_version="0.1.0",
        expected_adapter_config_sha256=SHA256,
        expected_network_policy_version="deny-all-1",
        requested_case_count=1,
        requested_image_count=1,
        requested_network_calls=0,
        requested_total_image_bytes=1000,
        requested_wall_time_seconds=30,
        now_utc=NOW,
    )
    kw.update(over)
    return kw


def _evaluate(**over):
    return evaluate_authorization(**_valid_kwargs(**over))


# ---- validate context ----
def test_valid_context_accepted():
    assert validate_authorization_context(
        _valid_context(), now_utc=NOW) == []


def test_context_none_rejected():
    assert validate_authorization_context(None, now_utc=NOW) == [
        "authorization_context_missing"]


def test_invalid_schema_rejected():
    ctx = _valid_context(schema_version="authorization-context-v0")
    assert "authorization_context_invalid" in validate_authorization_context(
        ctx, now_utc=NOW)


def test_invalid_authorization_id_rejected():
    ctx = _valid_context(authorization_id="auth-xyz")
    assert "authorization_id_invalid" in validate_authorization_context(
        ctx, now_utc=NOW)


def test_invalid_scope_rejected():
    ctx = _valid_context(authorization_scope="production")
    assert "authorization_scope_mismatch" in validate_authorization_context(
        ctx, now_utc=NOW)


def test_invalid_execution_mode_rejected():
    ctx = _valid_context(execution_mode="live")
    assert "authorization_execution_mode_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_invalid_approved_at_rejected():
    ctx = _valid_context(approved_at="not-a-time")
    assert "authorization_timestamp_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_invalid_expires_at_rejected():
    ctx = _valid_context(expires_at="2026-13-45T99:00:00Z")
    assert "authorization_timestamp_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_impossible_calendar_date_rejected():
    ctx = _valid_context(approved_at="2026-99-99T99:99:99Z")
    assert "authorization_timestamp_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_expires_at_not_after_approved_rejected():
    ctx = _valid_context(
        approved_at="2026-08-02T00:00:00Z",
        expires_at="2026-08-01T00:00:00Z")
    assert "authorization_expiry_not_after_approved" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_expired_context_rejected():
    ctx = _valid_context(expires_at="2026-07-01T00:00:00Z")
    assert "authorization_context_expired" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_malformed_commit_sha_rejected():
    ctx = _valid_context(commit_sha="xyz")
    assert "authorization_commit_sha_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_malformed_manifest_sha_rejected():
    ctx = _valid_context(dataset_manifest_sha256="short")
    assert "authorization_manifest_sha_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_malformed_config_sha_rejected():
    ctx = _valid_context(adapter_config_sha256="short")
    assert "authorization_config_sha_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_malformed_run_id_rejected():
    ctx = _valid_context(approved_run_id="run-nope")
    assert "authorization_run_id_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_negative_budget_rejected():
    ctx = _valid_context(max_case_count=-1)
    assert "authorization_budget_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_zero_budget_rejected():
    ctx = _valid_context(max_image_count=0)
    assert "authorization_budget_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_bool_as_budget_rejected():
    ctx = _valid_context(max_case_count=True)
    assert "authorization_budget_invalid" in \
        validate_authorization_context(ctx, now_utc=NOW)


def test_network_budget_nonzero_rejected():
    ctx = _valid_context(max_network_calls=5)
    assert "authorization_network_budget_nonzero" in \
        validate_authorization_context(ctx, now_utc=NOW)


# ---- binding ----
def test_repository_mismatch():
    d = _evaluate(expected_repository="other/repo")
    assert not d.authorized
    assert "authorization_binding_repository_mismatch" in d.fixed_error_codes


def test_branch_mismatch():
    d = _evaluate(expected_branch="master")
    assert not d.authorized
    assert "authorization_binding_branch_mismatch" in d.fixed_error_codes


def test_commit_mismatch():
    d = _evaluate(expected_commit_sha="f" * 40)
    assert "authorization_binding_commit_sha_mismatch" in d.fixed_error_codes


def test_manifest_mismatch():
    d = _evaluate(expected_manifest_sha256="f" * 64)
    assert "authorization_binding_manifest_sha256_mismatch" in \
        d.fixed_error_codes


def test_run_id_mismatch():
    d = _evaluate(expected_run_id="run-" + "e" * 12)
    assert "authorization_binding_run_id_mismatch" in d.fixed_error_codes


def test_loader_name_mismatch():
    d = _evaluate(expected_loader_name="other-loader")
    assert "authorization_binding_loader_name_mismatch" in \
        d.fixed_error_codes


def test_loader_version_mismatch():
    d = _evaluate(expected_loader_version="9.9.9")
    assert "authorization_binding_loader_version_mismatch" in \
        d.fixed_error_codes


def test_adapter_name_mismatch():
    d = _evaluate(expected_adapter_name="other-analyzer")
    assert "authorization_binding_adapter_name_mismatch" in \
        d.fixed_error_codes


def test_adapter_version_mismatch():
    d = _evaluate(expected_adapter_version="9.9.9")
    assert "authorization_binding_adapter_version_mismatch" in \
        d.fixed_error_codes


def test_adapter_config_mismatch():
    d = _evaluate(expected_adapter_config_sha256="f" * 64)
    assert "authorization_binding_adapter_config_sha256_mismatch" in \
        d.fixed_error_codes


def test_network_policy_mismatch():
    d = _evaluate(expected_network_policy_version="other-policy")
    assert "authorization_binding_network_policy_version_mismatch" in \
        d.fixed_error_codes


# ---- budget ----
def test_requested_cases_exceed_maximum():
    d = _evaluate(requested_case_count=11)  # max 10
    assert "authorization_budget_case_exceeded" in d.fixed_error_codes


def test_requested_images_exceed_maximum():
    d = _evaluate(requested_image_count=21)  # max 20
    assert "authorization_budget_image_exceeded" in d.fixed_error_codes


def test_requested_bytes_exceed_maximum():
    d = _evaluate(requested_total_image_bytes=2_000_000)  # max 1M
    assert "authorization_budget_bytes_exceeded" in d.fixed_error_codes


def test_requested_wall_time_exceed_maximum():
    d = _evaluate(requested_wall_time_seconds=301)  # max 300
    assert "authorization_budget_wall_time_exceeded" in d.fixed_error_codes


def test_requested_network_calls_greater_than_zero():
    d = _evaluate(requested_network_calls=1)
    assert not d.authorized
    assert "authorization_network_calls_forbidden" in d.fixed_error_codes


def test_exact_boundary_accepted():
    d = _evaluate(requested_case_count=10, requested_image_count=20,
                  requested_total_image_bytes=1_000_000,
                  requested_wall_time_seconds=300)
    assert d.authorized


# ---- gate ----
def test_all_valid_authorized_true():
    d = _evaluate()
    assert d.authorized is True
    assert d.fixed_error_codes == ()


def test_flag_absent_authorized_false():
    d = _evaluate(authorization_flag_present=False)
    assert not d.authorized
    assert "authorization_flag_missing" in d.fixed_error_codes


def test_env_absent_authorized_false():
    d = _evaluate(authorization_env_present=False)
    assert not d.authorized
    assert "authorization_env_missing" in d.fixed_error_codes


def test_env_rejected_authorized_false():
    d = _evaluate(authorization_env_accepted=False)
    assert not d.authorized
    assert "authorization_env_not_accepted" in d.fixed_error_codes


def test_context_absent_authorized_false():
    d = _evaluate(authorization_context=None)
    assert not d.authorized
    assert "authorization_context_missing" in d.fixed_error_codes


def test_context_invalid_authorized_false():
    ctx = _valid_context(authorization_id="bad")
    d = _evaluate(authorization_context=ctx)
    assert not d.authorized
    assert "authorization_id_invalid" in d.fixed_error_codes


def test_context_expired_authorized_false():
    ctx = _valid_context(expires_at="2026-07-01T00:00:00Z")
    d = _evaluate(authorization_context=ctx)
    assert not d.authorized
    assert "authorization_context_expired" in d.fixed_error_codes


def test_fixed_error_codes_order_stable():
    d1 = _evaluate(expected_repository="x", expected_branch="y")
    d2 = _evaluate(expected_repository="x", expected_branch="y")
    assert list(d1.fixed_error_codes) == list(d2.fixed_error_codes)


def test_fixed_error_codes_unique():
    d = _evaluate(authorization_flag_present=False,
                  authorization_env_present=False,
                  authorization_env_accepted=False,
                  authorization_context=None)
    codes = list(d.fixed_error_codes)
    assert len(codes) == len(set(codes))


def test_same_input_deterministic():
    d1 = _evaluate()
    d2 = _evaluate()
    assert d1.to_dict() == d2.to_dict()


# ---- digest ----
def test_same_context_same_digest():
    c1 = _valid_context()
    c2 = _valid_context()
    assert compute_authorization_context_digest(c1) == \
        compute_authorization_context_digest(c2)


def test_changed_field_changes_digest():
    c1 = _valid_context()
    c2 = _valid_context(branch="other-branch")
    assert compute_authorization_context_digest(c1) != \
        compute_authorization_context_digest(c2)


def test_digest_64_lowercase_hex():
    d = compute_authorization_context_digest(_valid_context())
    assert len(d) == 64
    assert all(c in "0123456789abcdef" for c in d)


def test_no_sensitive_value_in_serialized_output():
    raw = json.dumps(_valid_context(
        adapter_name="fake-analyzer").__dict__)
    assert "sk-" not in raw
    assert "token" not in raw.lower() or "authorization" in raw
    assert "secure-store://" not in raw
    assert "@" not in raw  # 無 email


# ---- decision 欄位 ----
def test_decision_fields_populated():
    d = _evaluate()
    assert d.authorization_flag_present is True
    assert d.authorization_env_present is True
    assert d.authorization_env_accepted is True
    assert d.authorization_context_present is True
    assert d.authorization_context_valid is True
    assert len(d.authorization_context_digest) == 64


# ================================================================
# Phase 6.4C2-B2-A — Zero-call / zero-secret-env proofs
# ================================================================
def test_zero_network_calls_all_flows(monkeypatch):
    import socket
    import urllib.request
    import http.client
    from alkaid_cs2.evaluation.network_policy import (
        NetworkPolicyV1, assert_network_disabled, validate_network_policy)
    from alkaid_cs2.evaluation.analyzer_audit import validate_audit_manifest

    def _v3_local(**over):
        a = {
            "schema_version": "external-analyzer-audit-v3",
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
            "authorization_context_digest": "b" * 64,
            "network_policy_version": "deny-all-1",
            "eligible_case_count": 1, "eligible_image_count": 1,
            "processed_image_count": 1, "attempted_image_count": 1,
            "succeeded_image_count": 1, "failed_image_count": 0,
            "cache_hit_count": 0, "cache_miss_count": 1,
            "cache_invalid_count": 0, "cache_write_count": 1,
            "requested_network_call_count": 0,
            "allowed_network_call_count": 0,
            "result": "completed", "fixed_error_codes": [],
            "image_hash_hashes": ["a" * 64],
            "analyzer_name": "fake-analyzer",
            "analyzer_version": "0.1.0",
        }
        a.update(over)
        return a

    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("network call attempted")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    # B2-A.1：全部主要流程（含 NetworkPolicy 與 Audit v3 success/blocked）
    ctx = _valid_context()
    assert validate_authorization_context(ctx, now_utc=NOW) == []
    d = _evaluate()
    assert d.authorized
    assert compute_authorization_context_digest(ctx)
    policy = NetworkPolicyV1()
    assert validate_network_policy(policy) == []
    assert assert_network_disabled(policy) == []
    v3_success = _v3_local()
    assert validate_audit_manifest(v3_success) == []
    v3_blocked = _v3_local(
        authorization_context_present=False,
        authorization_context_valid=False,
        authorization_decision=False,
        authorization_context_digest="")
    assert validate_audit_manifest(v3_blocked) == []
    assert calls["n"] == 0, "所有流程不得有任何網路呼叫"


def test_zero_secret_env_reads_all_flows(monkeypatch):
    calls = {"n": 0}
    real_getenv = os.getenv
    real_environ_get = os.environ.get
    real_environ_getitem = os.environ.__getitem__

    def spy_getenv(k, *a):
        if any(s in k.upper() for s in (
                "KEY", "TOKEN", "COOKIE", "SECRET", "ENDPOINT",
                "PASSWORD", "PROXY")):
            calls["n"] += 1
        return real_getenv(k, *a)

    def spy_environ_get(k, *a):
        if any(s in k.upper() for s in (
                "KEY", "TOKEN", "COOKIE", "SECRET", "ENDPOINT",
                "PASSWORD", "PROXY")):
            calls["n"] += 1
        return real_environ_get(k, *a)

    def spy_getitem(k):
        if any(s in k.upper() for s in (
                "KEY", "TOKEN", "COOKIE", "SECRET", "ENDPOINT",
                "PASSWORD", "PROXY")):
            calls["n"] += 1
        return real_environ_getitem(k)

    os.getenv = spy_getenv
    os.environ.get = spy_environ_get
    os.environ.__getitem__ = spy_getitem
    try:
        ctx = _valid_context()
        validate_authorization_context(ctx, now_utc=NOW)
        _evaluate()
        compute_authorization_context_digest(ctx)
        assert calls["n"] == 0, "不得讀取 secret env"
    finally:
        os.getenv = real_getenv
        os.environ.get = real_environ_get
        os.environ.__getitem__ = real_environ_getitem


# ================================================================
# Phase 6.4C2-B2-A.1 — Final Authorization Hardening
# ================================================================
# ---- requested budget 型別/負數 ----
def test_requested_case_count_negative_rejected():
    d = _evaluate(requested_case_count=-1)
    assert not d.authorized
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes
    assert d.authorization_context_valid is False


def test_requested_image_count_negative_rejected():
    d = _evaluate(requested_image_count=-1)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_network_calls_negative_rejected():
    d = _evaluate(requested_network_calls=-1)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_total_image_bytes_negative_rejected():
    d = _evaluate(requested_total_image_bytes=-1)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_wall_time_seconds_negative_rejected():
    d = _evaluate(requested_wall_time_seconds=-1)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_case_count_true_rejected():
    d = _evaluate(requested_case_count=True)
    assert not d.authorized
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_image_count_false_rejected():
    d = _evaluate(requested_image_count=False)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_network_calls_true_rejected():
    d = _evaluate(requested_network_calls=True)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_total_image_bytes_string_rejected():
    d = _evaluate(requested_total_image_bytes="100")
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


def test_requested_wall_time_seconds_none_rejected():
    d = _evaluate(requested_wall_time_seconds=None)
    assert "authorization_requested_budget_invalid" in d.fixed_error_codes


# ---- gate 型別 ----
def test_flag_one_rejected():
    d = _evaluate(authorization_flag_present=1)
    assert not d.authorized
    assert "authorization_gate_type_invalid" in d.fixed_error_codes
    assert d.authorization_flag_present is False  # 不得存 int
    assert d.authorization_context_valid is False  # B2-A.2


def test_env_present_string_true_rejected():
    d = _evaluate(authorization_env_present="true")
    assert "authorization_gate_type_invalid" in d.fixed_error_codes
    assert d.authorization_env_present is False
    assert d.authorization_context_valid is False  # B2-A.2


def test_env_accepted_none_rejected():
    d = _evaluate(authorization_env_accepted=None)
    assert "authorization_gate_type_invalid" in d.fixed_error_codes
    assert d.authorization_env_accepted is False
    assert d.authorization_context_valid is False  # B2-A.2


# ---- now_utc ----
def test_invalid_now_utc_rejected():
    ctx = _valid_context()
    errs = validate_authorization_context(ctx, now_utc="2026-99-99T99:99:99Z")
    assert "authorization_now_utc_invalid" in errs


def test_invalid_now_utc_no_expired_false_positive():
    # now_utc 無效 → 不得誤報 expired
    ctx = _valid_context()
    errs = validate_authorization_context(ctx, now_utc="not-a-time")
    assert "authorization_now_utc_invalid" in errs
    assert "authorization_context_expired" not in errs


def test_expired_uses_datetime_comparison():
    # 邊界：expires == now → 過期（datetime 比較）
    ctx = _valid_context(expires_at=NOW)
    errs = validate_authorization_context(ctx, now_utc=NOW)
    assert "authorization_context_expired" in errs


# ---- context_valid 語意 ----
def test_repository_mismatch_context_valid_false():
    d = _evaluate(expected_repository="other/repo")
    assert d.authorization_context_valid is False
    assert not d.authorized


def test_branch_mismatch_context_valid_false():
    d = _evaluate(expected_branch="master")
    assert d.authorization_context_valid is False


def test_commit_mismatch_context_valid_false():
    d = _evaluate(expected_commit_sha="f" * 40)
    assert d.authorization_context_valid is False


def test_budget_exceeded_context_valid_false():
    d = _evaluate(requested_case_count=99)
    assert d.authorization_context_valid is False


def test_requested_budget_invalid_context_valid_false():
    d = _evaluate(requested_network_calls=True)
    assert d.authorization_context_valid is False


def test_requested_network_calls_positive_context_valid_false():
    d = _evaluate(requested_network_calls=1)
    assert d.authorization_context_valid is False


def test_flag_absent_context_valid_still_true():
    # flag 缺 → authorized=False 但 context_valid=True（context/binding/budget 全合法）
    d = _evaluate(authorization_flag_present=False)
    assert d.authorization_context_valid is True
    assert d.authorized is False


def test_env_absent_context_valid_still_true():
    d = _evaluate(authorization_env_present=False)
    assert d.authorization_context_valid is True
    assert d.authorized is False


def test_env_rejected_context_valid_still_true():
    d = _evaluate(authorization_env_accepted=False)
    assert d.authorization_context_valid is True
    assert d.authorized is False


# ---- allowlist 全覆蓋 ----
def test_all_decision_codes_in_allowlist():
    from alkaid_cs2.evaluation.authorization_context import (
        AUTHORIZATION_ALL_ERROR_CODES,
    )
    # 收集所有可能輸出的碼
    cases = [
        _evaluate(authorization_flag_present=1),
        _evaluate(authorization_env_present=False),
        _evaluate(authorization_env_accepted=False),
        _evaluate(authorization_context=None),
        _evaluate(authorization_context=_valid_context(authorization_id="x")),
        _evaluate(expected_repository="x"),
        _evaluate(expected_branch="x"),
        _evaluate(expected_commit_sha="f" * 40),
        _evaluate(expected_manifest_sha256="f" * 64),
        _evaluate(expected_run_id="run-" + "e" * 12),
        _evaluate(expected_loader_name="x"),
        _evaluate(expected_loader_version="x"),
        _evaluate(expected_adapter_name="x"),
        _evaluate(expected_adapter_version="x"),
        _evaluate(expected_adapter_config_sha256="f" * 64),
        _evaluate(expected_network_policy_version="x"),
        _evaluate(requested_case_count=99),
        _evaluate(requested_image_count=99),
        _evaluate(requested_total_image_bytes=9_999_999),
        _evaluate(requested_wall_time_seconds=9999),
        _evaluate(requested_network_calls=5),
        _evaluate(requested_case_count=-1),
        _evaluate(requested_network_calls=True),
        _evaluate(requested_total_image_bytes="100"),
        _evaluate(authorization_context=_valid_context(execution_mode="live")),
        _evaluate(authorization_context=_valid_context(
            approved_at="2026-99-99T99:99:99Z")),
        _evaluate(authorization_context=_valid_context(
            expires_at="2026-07-01T00:00:00Z")),
    ]
    for d in cases:
        for c in d.fixed_error_codes:
            assert c in AUTHORIZATION_ALL_ERROR_CODES, f"{c} 不在 allowlist"


def test_fixed_error_codes_unique_and_ordered_b21():
    d = _evaluate(authorization_flag_present=False,
                  authorization_env_present=False,
                  authorization_env_accepted=False,
                  authorization_context=None)
    codes = list(d.fixed_error_codes)
    assert len(codes) == len(set(codes))
    assert codes == list(dict.fromkeys(codes))  # 保序


def test_decision_deterministic_b21():
    d1 = _evaluate(requested_network_calls=1)
    d2 = _evaluate(requested_network_calls=1)
    assert d1.to_dict() == d2.to_dict()


# ================================================================
# Phase 6.4C2-B2-A.2 — gate type vs context_valid 契約
# ================================================================
def test_false_boolean_gates_do_not_invalidate_context():
    # gate 值 False（型別合法）→ context_valid 保持 True、authorized False
    for kw in (dict(authorization_flag_present=False),
               dict(authorization_env_present=False),
               dict(authorization_env_accepted=False)):
        d = _evaluate(**kw)
        assert d.authorization_context_valid is True, kw
        assert d.authorized is False, kw


def test_gate_type_error_order_unique_deterministic():
    # 三 gate 型別同時不合法
    d1 = _evaluate(authorization_flag_present=1,
                   authorization_env_present="true",
                   authorization_env_accepted=None)
    codes1 = list(d1.fixed_error_codes)
    assert codes1.count("authorization_gate_type_invalid") == 1, codes1
    assert d1.authorization_context_valid is False
    assert d1.authorized is False
    assert codes1 == list(dict.fromkeys(codes1))  # 保序唯一
    d2 = _evaluate(authorization_flag_present=1,
                   authorization_env_present="true",
                   authorization_env_accepted=None)
    assert d2.fixed_error_codes == d1.fixed_error_codes  # deterministic
    assert d2.to_dict() == d1.to_dict()
