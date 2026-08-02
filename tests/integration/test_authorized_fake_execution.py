# -*- coding: utf-8 -*-
"""test_authorized_fake_execution.py — Phase 6.4C2-B2-B0 authorized wrapper"""
import hashlib
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.authorization_context import (  # noqa: E402
    AUTHORIZATION_SCHEMA_VERSION,
    AuthorizationContextV1,
    AuthorizationExecutionInputV1,
)
from alkaid_cs2.evaluation.network_policy import (  # noqa: E402
    NetworkPolicyV1,
)
from alkaid_cs2.evaluation.external_analyzer_runner import (  # noqa: E402
    EligibleAnalyzerCase,
    build_execution_plan,
    execute_authorized_external_analyzer_plan,
)
from alkaid_cs2.evaluation.external_analyzer_adapter import (  # noqa: E402
    FakeExternalAnalyzerAdapter,
)
from alkaid_cs2.evaluation.secure_image_loader import (  # noqa: E402
    InMemorySecureImageLoader,
)
from alkaid_cs2.evaluation.analyzer_audit import (  # noqa: E402
    AUDIT_SCHEMA_VERSION_V3,
    validate_audit_manifest,
)

NOW = "2026-08-01T12:00:00Z"
SHA1 = "a" * 40
SHA256 = "b" * 64
FAKE_SHA = hashlib.sha256(b"fake-image-001").hexdigest()
FAKE_BYTES = b"fake-image-001"


def _context(**over):
    ctx = dict(
        schema_version=AUTHORIZATION_SCHEMA_VERSION,
        authorization_id="auth-" + "d" * 16,
        authorization_scope="evaluation",
        approved_at="2026-08-01T00:00:00Z",
        expires_at="2026-08-02T00:00:00Z",
        repository="Alkaid-Delta/Alkaid-CS2",
        branch="agent/v2-vision-real-evaluation",
        commit_sha=SHA1,
        dataset_manifest_sha256=SHA256,
        execution_mode="contract_only",
        approved_run_id="",  # wrapper 用實際 plan.run_id 驗證
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


class _SpyLoader(InMemorySecureImageLoader):
    def __init__(self, objects):
        super().__init__(objects)
        self.load_calls = 0

    def load(self, storage_reference, expected_sha256):
        self.load_calls += 1
        return super().load(storage_reference, expected_sha256)


class _SpyAdapter(FakeExternalAnalyzerAdapter):
    def __init__(self):
        super().__init__()
        self.analyze_calls = 0

    def analyze_image(self, image_bytes, *, case_key, image_index):
        self.analyze_calls += 1
        return super().analyze_image(
            image_bytes, case_key=case_key, image_index=image_index)


def _env():
    import tempfile
    base = tempfile.mkdtemp(prefix="b2b0-")
    return (base, os.path.join(base, "local_data", "cache"),
            os.path.join(base, "local_data", "runs"))


def _case(case_id="case-w", refs=("secure-store://img-1",),
          hashes=(FAKE_SHA,)):
    return EligibleAnalyzerCase(
        source_case_id=case_id, source="manual_fixture",
        storage_references=list(refs), image_hashes=list(hashes),
        review_status="double_review", privacy_scan_status="passed")


_NO_CTX = object()


def _run(case=None, *, context=_NO_CTX, input_over=None, plan_over=None,
         loader=None, adapter=None):
    case = case or _case()
    plan = build_execution_plan(
        eligible_cases=[{
            "case_id": case.source_case_id, "source": case.source,
            "storage_references": list(case.storage_references),
            "image_hashes": list(case.image_hashes),
            "review_status": case.review_status,
            "privacy_scan_status": case.privacy_scan_status}],
        run_salt="salt-1", adapter_name="fake-analyzer",
        dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    if plan_over:
        plan_over(plan)
    ctx = (_context(approved_run_id=plan.run_id)
           if context is _NO_CTX else context)
    inp = dict(
        authorization_flag_present=True,
        authorization_env_present=True,
        authorization_env_accepted=True,
        authorization_context=ctx,
        network_policy=NetworkPolicyV1(),
        expected_repository="Alkaid-Delta/Alkaid-CS2",
        expected_branch="agent/v2-vision-real-evaluation",
        expected_commit_sha=SHA1,
        expected_manifest_sha256=SHA256,
        expected_run_id=plan.run_id,
        expected_loader_name="in-memory-loader",
        expected_loader_version="1.0.0",
        expected_adapter_name="fake-analyzer",
        expected_adapter_version="0.1.0",
        expected_adapter_config_sha256=SHA256,
        expected_network_policy_version="deny-all-1",
        requested_case_count=1,
        requested_image_count=len(case.image_hashes),
        requested_network_calls=0,
        requested_total_image_bytes=1000,
        requested_wall_time_seconds=30,
        now_utc=NOW,
    )
    if input_over:
        inp.update(input_over)
    authorization_input = AuthorizationExecutionInputV1(**inp)
    loader = loader or _SpyLoader({"secure-store://img-1": FAKE_BYTES})
    adapter = adapter or _SpyAdapter()
    root, cache, runs = _env()
    s = execute_authorized_external_analyzer_plan(
        plan=plan, eligible_cases=[case], loader=loader, adapter=adapter,
        cache_dir=cache, audit_dir=runs, allowed_root=root,
        analyzer_name="fake-analyzer", analyzer_version="0.1.0",
        authorization_input=authorization_input)
    return s, loader, adapter, runs, plan


def _read_v3(runs, run_id):
    return json.load(open(
        os.path.join(runs, "v3", run_id, "audit.json"),
        encoding="utf-8"))


# ---- A. 授權成功 ----
def test_authorized_fake_execution_completed():
    s, loader, adapter, runs, plan = _run()
    assert s.status == "completed"
    assert loader.load_calls == 1
    assert adapter.analyze_calls == 1
    assert s.cache_write_count == 1
    a = _read_v3(runs, s.run_id)
    assert validate_audit_manifest(a) == []
    assert a["schema_version"] == AUDIT_SCHEMA_VERSION_V3
    assert a["authorization_decision"] is True
    assert a["allowed_network_call_count"] == 0


# ---- B. 不信任 plan.authorized ----
def test_plan_authorized_true_context_none_blocked():
    s, loader, adapter, runs, plan = _run(
        context=None, input_over={"authorization_context": None})
    assert s.status == "blocked"
    assert loader.load_calls == 0
    assert adapter.analyze_calls == 0
    assert s.cache_write_count == 0
    assert "authorization_context_missing" in s.fixed_error_codes
    a = _read_v3(runs, s.run_id)
    assert a["authorization_decision"] is False
    assert a["authorization_context_present"] is False
    assert a["authorization_context_digest"] == ""


# ---- C/D/E. flag/env ----
def test_flag_missing_blocked():
    s, loader, adapter, runs, plan = _run(
        input_over={"authorization_flag_present": False})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert "authorization_flag_missing" in s.fixed_error_codes


def test_env_missing_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"authorization_env_present": False})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0


def test_env_rejected_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"authorization_env_accepted": False})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0


# ---- F. gate type invalid ----
def test_gate_type_invalid_blocked():
    s, loader, adapter, runs, plan = _run(
        input_over={"authorization_flag_present": 1})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert "authorization_gate_type_invalid" in s.fixed_error_codes
    a = _read_v3(runs, s.run_id)
    assert a["authorization_context_valid"] is False
    assert a["authorization_decision"] is False


# ---- G-O. binding mismatches ----
def test_context_expired_blocked():
    ctx = _context(approved_run_id="", expires_at="2026-07-01T00:00:00Z")
    s, loader, adapter, _, _ = _run(context=ctx)
    assert s.status == "blocked"
    assert "authorization_context_expired" in s.fixed_error_codes


def test_repository_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_repository": "other/repo"})
    assert s.status == "blocked" and loader.load_calls == 0


def test_branch_mismatch_blocked():
    s, loader, adapter, _, _ = _run(input_over={"expected_branch": "master"})
    assert s.status == "blocked" and loader.load_calls == 0


def test_commit_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_commit_sha": "f" * 40})
    assert s.status == "blocked" and loader.load_calls == 0


def test_manifest_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_manifest_sha256": "f" * 64})
    assert s.status == "blocked" and loader.load_calls == 0


def test_run_id_mismatch_blocked():
    # approved_run_id 與實際 plan.run_id 不符（wrapper 以 plan.run_id 為真）
    ctx = _context(approved_run_id="run-" + "e" * 12)
    s, loader, adapter, _, _ = _run(context=ctx)
    assert s.status == "blocked"
    assert "authorization_binding_run_id_mismatch" in s.fixed_error_codes


def test_loader_identity_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_loader_name": "other-loader"})
    assert s.status == "blocked" and loader.load_calls == 0


def test_adapter_identity_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_adapter_version": "9.9.9"})
    assert s.status == "blocked" and adapter.analyze_calls == 0


def test_network_policy_version_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_network_policy_version": "other"})
    assert s.status == "blocked"


# ---- P/Q. network policy violations ----
def test_allow_network_true_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"network_policy": NetworkPolicyV1(allow_network=True)})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert "network_policy_not_deny_all" in s.fixed_error_codes


def test_destinations_nonempty_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"network_policy": NetworkPolicyV1(
            allowed_destination_ids=("api.example.com",))})
    assert s.status == "blocked"
    assert "network_policy_destination_not_empty" in s.fixed_error_codes


# ---- R-T. requested mismatches ----
def test_requested_network_calls_one_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"requested_network_calls": 1})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0


def test_requested_case_count_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"requested_case_count": 5})
    assert s.status == "blocked"
    assert ("authorization_requested_case_count_mismatch"
            in s.fixed_error_codes)


def test_requested_image_count_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"requested_image_count": 7})
    assert s.status == "blocked"
    assert ("authorization_requested_image_count_mismatch"
            in s.fixed_error_codes)


def test_budget_exceeded_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"requested_case_count": 99,
                    "requested_image_count": 99})
    assert s.status == "blocked"
    assert "authorization_budget_case_exceeded" in s.fixed_error_codes


# ---- V/W. audit v3 合法 ----
def test_blocked_audit_v3_valid():
    _, _, _, runs, plan = _run(context=None)
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["result"] == "blocked"


def test_authorized_audit_v3_valid():
    s, _, _, runs, plan = _run()
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["result"] == "completed"
    assert a["requested_network_call_count"] == 0
    assert a["allowed_network_call_count"] == 0
    assert a["network_policy_version"] == "deny-all-1"


# ---- X. context absent digest 空 ----
def test_context_absent_digest_empty_in_audit():
    _, _, _, runs, plan = _run(context=None)
    a = _read_v3(runs, plan.run_id)
    assert a["authorization_context_digest"] == ""


# ---- Y/Z. zero secret env / zero network ----
def test_zero_secret_env_reads(monkeypatch):
    calls = {"n": 0}
    real_getenv = os.getenv
    real_environ_get = os.environ.get
    real_getitem = os.environ.__getitem__

    def spy(k, *a):
        if any(x in k.upper() for x in (
                "KEY", "TOKEN", "COOKIE", "SECRET", "ENDPOINT",
                "PASSWORD", "PROXY")):
            calls["n"] += 1
        return real_getenv(k, *a) if a else None

    os.getenv = spy
    os.environ.get = lambda k, *a: (calls.__setitem__(
        "n", calls["n"] + 1) if any(x in k.upper() for x in (
            "KEY", "TOKEN", "COOKIE", "SECRET", "ENDPOINT",
            "PASSWORD", "PROXY")) else None) or real_environ_get(k, *a)
    os.environ.__getitem__ = lambda k: calls.__setitem__(
        "n", calls["n"] + 1) or real_getitem(k)
    try:
        # 多種 flow（blocked + authorized）
        _run()
        _run(context=None)
        _run(input_over={"network_policy": NetworkPolicyV1(
            allow_network=True)})
        _run(input_over={"expected_branch": "x"})
        assert calls["n"] == 0, f"secret env read: {calls['n']}"
    finally:
        os.getenv = real_getenv
        os.environ.get = real_environ_get
        os.environ.__getitem__ = real_getitem


def test_zero_network_calls(monkeypatch):
    import socket
    import urllib.request
    import http.client
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("network call")

    monkeypatch.setattr(socket, "socket", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(urllib.request, "urlopen", boom)
    monkeypatch.setattr(http.client, "HTTPConnection", boom)
    _run()
    _run(context=None)
    _run(input_over={"network_policy": NetworkPolicyV1(
        allow_network=True)})
    _run(input_over={"expected_branch": "x"})
    _run(input_over={"requested_network_calls": 1})
    assert calls["n"] == 0


# ---- AA. blocked 不呼叫 legacy B1 engine ----
def test_blocked_does_not_call_legacy_engine(monkeypatch):
    import alkaid_cs2.evaluation.external_analyzer_runner as runner
    calls = {"n": 0}
    real_engine = runner.execute_external_analyzer_plan

    def spy_engine(*a, **k):
        calls["n"] += 1
        return real_engine(*a, **k)

    monkeypatch.setattr(runner, "execute_external_analyzer_plan",
                        spy_engine)
    _run(context=None)
    _run(input_over={"authorization_flag_present": False})
    _run(input_over={"expected_branch": "x"})
    assert calls["n"] == 0, "blocked 不得委派 legacy engine"


# ================================================================
# Phase 6.4C2-B2-B0.1 — plan.authorized 無關 / plan preflight / invalid policy
# ================================================================
def test_plan_authorized_false_still_completed():
    # 正式 authorization 全合法 + plan.authorized=False → 仍 completed
    def set_false(plan):
        plan.authorized = False
    s, loader, adapter, runs, plan = _run(plan_over=set_false)
    assert s.status == "completed"
    assert loader.load_calls == 1 and adapter.analyze_calls == 1
    a = _read_v3(runs, s.run_id)
    assert a["authorization_decision"] is True


def test_plan_authorized_true_still_completed():
    s, loader, adapter, runs, plan = _run()
    assert s.status == "completed"
    assert loader.load_calls == 1 and adapter.analyze_calls == 1


def test_plan_authorized_true_false_semantics_consistent():
    def set_false(plan):
        plan.authorized = False
    s1, l1, a1, _, _ = _run()
    s2, l2, a2, _, _ = _run(plan_over=set_false)
    for k in ("status", "processed_image_count", "attempted_image_count",
              "succeeded_image_count", "cache_write_count",
              "cache_miss_count"):
        assert getattr(s1, k) == getattr(s2, k), k
    assert l1.load_calls == l2.load_calls == 1
    assert a1.analyze_calls == a2.analyze_calls == 1


def test_invalid_context_plan_authorized_true_blocked():
    s, loader, adapter, _, _ = _run(context=None)
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0


# ---- plan structural preflight ----
def test_plan_case_count_mismatch_blocked():
    def bad(plan):
        plan.case_count = 99
    s, loader, adapter, runs, plan = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_count_mismatch" in s.fixed_error_codes
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert s.cache_write_count == 0


def test_plan_image_count_mismatch_blocked():
    def bad(plan):
        plan.image_count = 99
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_count_mismatch" in s.fixed_error_codes


def test_plan_expected_hashes_mismatch_blocked():
    def bad(plan):
        plan.expected_hashes = ["f" * 64]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_hash_mismatch" in s.fixed_error_codes


def test_plan_image_indexes_mismatch_blocked():
    def bad(plan):
        plan.image_indexes = [7]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_hash_mismatch" in s.fixed_error_codes


def test_plan_case_keys_length_mismatch_blocked():
    def bad(plan):
        plan.case_keys = []
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_count_mismatch" in s.fixed_error_codes


def test_duplicate_execution_item_blocked():
    def bad(plan):
        plan.case_keys = [plan.case_keys[0]] * 2
        plan.image_indexes = [0, 0]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "duplicate_execution_item" in s.fixed_error_codes


def test_plan_schema_invalid_blocked():
    def bad(plan):
        plan.schema_version = "legacy-v0"
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_plan_run_id_invalid_safe_blocked_audit():
    def bad(plan):
        plan.run_id = "EVIL-../../"
    s, loader, adapter, runs, plan = _run(plan_over=bad)
    assert s.status == "blocked"
    import re as _re
    assert _re.fullmatch(r"run-[0-9a-f]{12}", s.run_id)
    a = json.load(open(os.path.join(runs, "v3", s.run_id, "audit.json"),
                       encoding="utf-8"))
    assert validate_audit_manifest(a) == []
    assert a["authorization_decision"] is False
    assert "EVIL" not in json.dumps(a)


def test_plan_adapter_name_mismatch_blocked():
    def bad(plan):
        plan.adapter_name = "other-analyzer"
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_adapter_identity_mismatch" in s.fixed_error_codes


# ---- expected_run_id 雙重 binding ----
def test_expected_run_id_mismatch_blocked():
    s, loader, adapter, _, _ = _run(
        input_over={"expected_run_id": "run-" + "e" * 12})
    assert s.status == "blocked"
    assert "authorization_expected_run_id_mismatch" in s.fixed_error_codes


def test_context_approved_run_id_mismatch_blocked():
    ctx = _context(approved_run_id="run-" + "e" * 12)
    s, loader, adapter, _, _ = _run(context=ctx)
    assert s.status == "blocked"
    assert "authorization_binding_run_id_mismatch" in s.fixed_error_codes


# ---- invalid network policy → blocked Audit v3 成功 ----
def test_network_policy_none_blocked_audit_valid():
    s, loader, adapter, runs, plan = _run(
        input_over={"network_policy": None})
    assert s.status == "blocked"
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["network_policy_version"] == "invalid-policy"
    assert "network_policy_invalid" in " ".join(a["fixed_error_codes"])
    assert "audit_write_failed" not in a["fixed_error_codes"]


def test_network_policy_wrong_object_blocked_audit_valid():
    s, loader, adapter, runs, plan = _run(
        input_over={"network_policy": object()})
    assert s.status == "blocked"
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["network_policy_version"] == "invalid-policy"


def test_network_policy_string_blocked_audit_valid():
    s, loader, adapter, runs, plan = _run(
        input_over={"network_policy": "invalid"})
    assert s.status == "blocked"
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["network_policy_version"] == "invalid-policy"


def test_unsafe_policy_version_blocked_audit_valid():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    p = NetworkPolicyV1(policy_version="../../etc/passwd")
    s, loader, adapter, runs, plan = _run(input_over={"network_policy": p})
    assert s.status == "blocked"
    a = _read_v3(runs, plan.run_id)
    assert validate_audit_manifest(a) == []
    assert a["network_policy_version"] == "invalid-policy"
    assert "../../" not in json.dumps(a)


def test_invalid_policy_no_audit_write_failed():
    for bad in (None, object(), "invalid"):
        s, _, _, _, _ = _run(input_over={"network_policy": bad})
        assert s.status == "blocked"
        assert "audit_write_failed" not in s.fixed_error_codes, bad


# ---- decision/result 一致性 ----
def test_plan_error_decision_false():
    def bad(plan):
        plan.case_count = 99
    s, _, _, runs, plan = _run(plan_over=bad)
    a = _read_v3(runs, s.run_id)
    assert a["authorization_decision"] is False
    assert a["result"] == "blocked"
    assert any("count_mismatch" in c for c in a["fixed_error_codes"])


def test_plan_error_blocked_zero_calls():
    def bad(plan):
        plan.expected_hashes = ["f" * 64]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert s.cache_write_count == 0


def test_caller_plan_unchanged():
    case = _case()
    plan = build_execution_plan(
        eligible_cases=[{
            "case_id": case.source_case_id, "source": case.source,
            "storage_references": list(case.storage_references),
            "image_hashes": list(case.image_hashes),
            "review_status": case.review_status,
            "privacy_scan_status": case.privacy_scan_status}],
        run_salt="salt-1", adapter_name="fake-analyzer",
        dry_run=False, authorized=True,
        created_at="2026-08-01T00:00:00Z")
    before = (plan.authorized, plan.status, plan.dry_run)
    _run(plan_over=lambda p: setattr(p, "authorized", False))
    # 原始 plan 執行前後未被修改
    after = (plan.authorized, plan.status, plan.dry_run)
    assert after == before


def test_blocked_authorized_audits_both_valid():
    # blocked（plan error）與 authorized 都寫合法 v3
    def bad(plan):
        plan.case_count = 99
    s1, _, _, runs1, _ = _run(plan_over=bad)
    a1 = json.load(open(os.path.join(
        runs1, "v3", s1.run_id, "audit.json"), encoding="utf-8"))
    assert validate_audit_manifest(a1) == []
    s2, _, _, runs2, plan2 = _run()
    a2 = json.load(open(os.path.join(
        runs2, "v3", plan2.run_id, "audit.json"), encoding="utf-8"))
    assert validate_audit_manifest(a2) == []


# ================================================================
# Phase 6.4C2-B2-B0.2 — Defensive preflight / malformed inputs
# ================================================================
def _run_malformed(plan=None, inp=None, cases=None, **kw):
    """malformed 輸入直接呼叫 wrapper（不經 _run helper 的建構）。"""
    import tempfile
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        execute_authorized_external_analyzer_plan,
    )
    if plan is _NO_PLAN:
        plan = None
    if inp is _NO_INP:
        inp = None
    if cases is _NO_CASES:
        cases = None
    base = tempfile.mkdtemp(prefix="b2b02-")
    cache = os.path.join(base, "local_data", "cache")
    runs = os.path.join(base, "local_data", "runs")
    s = execute_authorized_external_analyzer_plan(
        plan=plan, eligible_cases=cases, loader=object(), adapter=object(),
        cache_dir=cache, audit_dir=runs, allowed_root=base,
        analyzer_name="fake-analyzer", analyzer_version="0.1.0",
        authorization_input=inp)
    return s, runs


_NO_PLAN = object()
_NO_INP = object()
_NO_CASES = object()


def _read_v3_path(runs, run_id):
    return json.load(open(
        os.path.join(runs, "v3", run_id, "audit.json"), encoding="utf-8"))


# ---- 1-5. top-level malformed ----
def test_plan_none_blocked():
    s, runs = _run_malformed(plan=None)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes
    a = _read_v3_path(runs, s.run_id)
    assert validate_audit_manifest(a) == []


def test_plan_object_blocked():
    s, _ = _run_malformed(plan=object())
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_plan_string_blocked():
    s, _ = _run_malformed(plan="invalid")
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_authorization_input_none_blocked():
    s, runs = _run_malformed(inp=None)
    assert s.status == "blocked"
    assert "authorization_execution_input_invalid" in s.fixed_error_codes
    a = _read_v3_path(runs, s.run_id)
    assert validate_audit_manifest(a) == []


def test_authorization_input_object_blocked():
    s, _ = _run_malformed(inp=object())
    assert s.status == "blocked"
    assert "authorization_execution_input_invalid" in s.fixed_error_codes


# ---- 6-10. malformed eligible ----
def test_eligible_none_blocked():
    s, runs = _run_malformed(cases=None)
    assert s.status == "blocked"
    assert "eligible_analyzer_cases_invalid" in s.fixed_error_codes
    a = _read_v3_path(runs, s.run_id)
    assert validate_audit_manifest(a) == []
    assert a["eligible_case_count"] == 0
    assert a["eligible_image_count"] == 0
    assert a["image_hash_hashes"] == []


def test_eligible_string_blocked():
    s, _ = _run_malformed(cases="invalid")
    assert s.status == "blocked"
    assert "eligible_analyzer_cases_invalid" in s.fixed_error_codes


def test_eligible_wrong_object_blocked():
    s, _ = _run_malformed(cases=[object()])
    assert s.status == "blocked"
    assert "eligible_analyzer_cases_invalid" in s.fixed_error_codes


def test_eligible_hashes_none_blocked():
    case = _case()
    case.image_hashes = None  # type: ignore
    s, _ = _run_malformed(cases=[case])
    assert s.status == "blocked"
    assert "eligible_analyzer_cases_invalid" in s.fixed_error_codes


def test_eligible_hashes_bool_int_blocked():
    case = _case()
    case.image_hashes = [True]  # type: ignore
    s, _ = _run_malformed(cases=[case])
    assert s.status == "blocked"
    assert "eligible_analyzer_cases_invalid" in s.fixed_error_codes


# ---- 11-14. plan counts 型別 ----
def test_plan_case_count_negative_blocked():
    def bad(plan):
        plan.case_count = -1
    s, loader, adapter, runs, plan = _run(plan_over=bad)
    assert s.status == "blocked"
    a = _read_v3(runs, s.run_id)
    assert validate_audit_manifest(a) == []


def test_plan_case_count_true_blocked():
    def bad(plan):
        plan.case_count = True
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_plan_image_count_negative_blocked():
    def bad(plan):
        plan.image_count = -1
    s, loader, adapter, runs, plan = _run(plan_over=bad)
    assert s.status == "blocked"
    assert validate_audit_manifest(_read_v3(runs, s.run_id)) == []


def test_plan_image_count_true_blocked():
    def bad(plan):
        plan.image_count = True
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"


# ---- 15-20. plan list 欄位型別 ----
def test_plan_case_keys_none_blocked():
    def bad(plan):
        plan.case_keys = None
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_plan_image_indexes_none_blocked():
    def bad(plan):
        plan.image_indexes = None
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"


def test_plan_expected_hashes_none_blocked():
    def bad(plan):
        plan.expected_hashes = None
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"


def test_plan_case_keys_non_string_blocked():
    def bad(plan):
        plan.case_keys = [123]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"
    assert "execution_plan_invalid" in s.fixed_error_codes


def test_plan_image_indexes_bool_blocked():
    def bad(plan):
        plan.image_indexes = [True]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"


def test_plan_expected_hashes_invalid_blocked():
    def bad(plan):
        plan.expected_hashes = ["not-a-hash"]
    s, loader, adapter, _, _ = _run(plan_over=bad)
    assert s.status == "blocked"


# ---- 21-22. malformed audit 完整性 ----
def test_malformed_plan_audit_safe_fields():
    def bad(plan):
        plan.run_id = "EVIL-../../"
    s, loader, adapter, runs, plan = _run(plan_over=bad)
    a = _read_v3(runs, s.run_id)
    assert validate_audit_manifest(a) == []
    import re as _re
    assert _re.fullmatch(r"run-[0-9a-f]{12}", a["run_id"])
    assert isinstance(a["eligible_case_count"], int) and         a["eligible_case_count"] >= 0
    assert isinstance(a["eligible_image_count"], int) and         a["eligible_image_count"] >= 0
    assert all(isinstance(h, str) and len(h) == 64 for h in
               a["image_hash_hashes"])
    assert a["authorization_decision"] is False


def test_malformed_eligible_audit_zero_counts():
    s, runs = _run_malformed(cases=None)
    a = _read_v3_path(runs, s.run_id)
    assert validate_audit_manifest(a) == []
    assert a["eligible_case_count"] == 0
    assert a["eligible_image_count"] == 0
    assert a["image_hash_hashes"] == []


# ---- 23. 零呼叫 + 無 audit_write_failed ----
def test_all_malformed_zero_calls_no_audit_write_failed():
    for plan, inp, cases in [
        (None, None, None),
        (object(), None, None),
        ("invalid", None, None),
        (None, object(), None),
        (None, None, "invalid"),
        (None, None, [object()]),
    ]:
        s, runs = _run_malformed(plan=plan, inp=inp, cases=cases)
        assert s.status == "blocked", (plan, inp, cases)
        assert s.cache_write_count == 0
        assert "audit_write_failed" not in s.fixed_error_codes
        a = _read_v3_path(runs, s.run_id)
        assert validate_audit_manifest(a) == []


# ---- 24. 真正 audit write failure ----
def test_real_audit_write_failure_reported(monkeypatch):
    import alkaid_cs2.evaluation.analyzer_audit as audit_mod
    real = audit_mod.write_audit_manifest

    def boom(*a, **k):
        raise audit_mod.AnalyzerAuditWriteError("simulated")

    monkeypatch.setattr(audit_mod, "write_audit_manifest", boom)
    s, loader, adapter, _, _ = _run(context=None)
    assert s.status == "blocked"
    assert "audit_write_failed" in s.fixed_error_codes
    assert loader.load_calls == 0 and adapter.analyze_calls == 0
    assert s.cache_write_count == 0


# ---- 25. source 不得以 except Exception 掩蓋 wrapper errors ----
def test_wrapper_no_broad_except():
    src = open(os.path.join(
        PROJECT_ROOT, "alkaid_cs2", "evaluation",
        "external_analyzer_runner.py"), encoding="utf-8").read()
    # wrapper 區塊內不得有 except Exception
    wrapper_section = src[src.index(
        "def execute_authorized_external_analyzer_plan("):]
    assert "except Exception" not in wrapper_section


# ================================================================
# Phase 6.4C2-B2-B0.3 — exact-type policy version / AST no-broad-except
# ================================================================
def _safe_ver(policy):
    from alkaid_cs2.evaluation.external_analyzer_runner import (
        _safe_network_policy_version,
    )
    return _safe_network_policy_version(policy)


def test_safe_policy_version_none():
    assert _safe_ver(None) == "invalid-policy"


def test_safe_policy_version_object():
    assert _safe_ver(object()) == "invalid-policy"


def test_safe_policy_version_string():
    assert _safe_ver("invalid") == "invalid-policy"


def test_safe_policy_version_valid_exact():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    assert _safe_ver(NetworkPolicyV1()) == "deny-all-1"


def test_safe_policy_version_wrong_schema():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    p = NetworkPolicyV1(schema_version="network-policy-v0")
    assert _safe_ver(p) == "invalid-policy"


def test_safe_policy_version_empty_version():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    p = NetworkPolicyV1(policy_version="")
    assert _safe_ver(p) == "invalid-policy"


def test_safe_policy_version_too_long():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    p = NetworkPolicyV1(policy_version="x" * 65)
    assert _safe_ver(p) == "invalid-policy"


def test_safe_policy_version_unsafe_chars():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1
    p = NetworkPolicyV1(policy_version="../../etc/passwd")
    assert _safe_ver(p) == "invalid-policy"


def test_safe_policy_version_subclass_rejected():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1

    class SubPolicy(NetworkPolicyV1):
        pass

    assert _safe_ver(SubPolicy()) == "invalid-policy"


def test_safe_policy_version_malicious_subclass_not_accessed():
    from alkaid_cs2.evaluation.network_policy import NetworkPolicyV1

    class RaisingPolicy(NetworkPolicyV1):
        @property
        def policy_version(self):
            raise RuntimeError("must not be accessed")

    # frozen dataclass 的 property 覆寫無法走 __init__ → object.__new__ 建構
    # （exact-type 判斷在屬性存取前 → property 不觸發、不 raise）
    obj = RaisingPolicy.__new__(RaisingPolicy)
    assert _safe_ver(obj) == "invalid-policy"


# ---- AST 防退化（涵蓋全部 B2-B0 security functions）----
_B2B0_SECURITY_FUNCS = (
    "_safe_network_policy_version",
    "collect_safe_eligible_facts",
    "_safe_plan_run_id",
    "validate_authorized_execution_plan",
    "_build_v3_audit",
    "execute_authorized_external_analyzer_plan",
    "_write_blocked_v3_audit",
)


def test_ast_no_broad_except_in_b2b0_helpers():
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "alkaid_cs2", "evaluation",
        "external_analyzer_runner.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, _ast.FunctionDef)}
    missing = [f for f in _B2B0_SECURITY_FUNCS if f not in funcs]
    assert not missing, f"缺少 security function：{missing}"
    bad = []
    for name in _B2B0_SECURITY_FUNCS:
        fn = funcs[name]
        for node in _ast.walk(fn):
            if isinstance(node, _ast.ExceptHandler):
                if node.type is None:
                    bad.append(f"{name}: bare except")
                elif isinstance(node.type, _ast.Name) and                         node.type.id in ("Exception", "BaseException"):
                    bad.append(f"{name}: except {node.type.id}")
                elif isinstance(node.type, _ast.Tuple):
                    for elt in node.type.elts:
                        if isinstance(elt, _ast.Name) and                                 elt.id in ("Exception", "BaseException"):
                            bad.append(
                                f"{name}: except tuple 含 {elt.id}")
    assert not bad, f"broad except 違規：{bad}"


def test_ast_allowed_exceptions_only_audit_boundary():
    import ast as _ast
    src = open(os.path.join(
        PROJECT_ROOT, "alkaid_cs2", "evaluation",
        "external_analyzer_runner.py"), encoding="utf-8").read()
    tree = _ast.parse(src)
    funcs = {n.name: n for n in tree.body
             if isinstance(n, _ast.FunctionDef)}
    for name in _B2B0_SECURITY_FUNCS:
        fn = funcs[name]
        for node in _ast.walk(fn):
            if isinstance(node, _ast.ExceptHandler) and node.type is not None:
                if isinstance(node.type, _ast.Name):
                    assert node.type.id in ("AnalyzerAuditWriteError",
                                            "ValueError"), \
                        f"{name}: 未允許的 except {node.type.id}"
                elif isinstance(node.type, _ast.Tuple):
                    for elt in node.type.elts:
                        assert (isinstance(elt, _ast.Name) and
                                elt.id in ("AnalyzerAuditWriteError",
                                           "ValueError")), \
                            f"{name}: tuple except 含未允許型別"
