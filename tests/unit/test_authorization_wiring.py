# -*- coding: utf-8 -*-
"""test_authorization_wiring.py — Phase 6.4C2-B2-B0 authorization wiring"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.authorization_context import (  # noqa: E402
    AUTHORIZATION_SCHEMA_VERSION,
    AuthorizationContextV1,
    AuthorizationExecutionInputV1,
    evaluate_execution_authorization,
)
from alkaid_cs2.evaluation.network_policy import (  # noqa: E402
    NetworkPolicyV1,
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


def _valid_input(**over):
    inp = dict(
        authorization_flag_present=True,
        authorization_env_present=True,
        authorization_env_accepted=True,
        authorization_context=_valid_context(),
        network_policy=NetworkPolicyV1(),
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
    inp.update(over)
    return AuthorizationExecutionInputV1(**inp)


def _evaluate(inp=None, plan_run_id=RUN_ID, case_count=1, image_count=1):
    inp = inp or _valid_input()
    return evaluate_execution_authorization(
        authorization_input=inp, plan_run_id=plan_run_id,
        eligible_case_count=case_count, eligible_image_count=image_count)


# ---- 成功 ----
def test_valid_input_authorized():
    d = _evaluate()
    assert d.authorized is True
    assert d.fixed_error_codes == ()


# ---- 不信任 plan.authorized（context=None → blocked）----
def test_plan_authorized_not_trusted_without_context():
    d = _evaluate(_valid_input(authorization_context=None))
    assert d.authorized is False
    assert "authorization_context_missing" in d.fixed_error_codes
    assert d.authorization_context_present is False
    assert d.authorization_context_valid is False


# ---- gate ----
def test_flag_missing_context_valid_still_true():
    d = _evaluate(_valid_input(authorization_flag_present=False))
    assert d.authorized is False
    assert "authorization_flag_missing" in d.fixed_error_codes
    assert d.authorization_context_valid is True


def test_env_missing_blocked():
    d = _evaluate(_valid_input(authorization_env_present=False))
    assert d.authorized is False
    assert "authorization_env_missing" in d.fixed_error_codes


def test_env_rejected_blocked():
    d = _evaluate(_valid_input(authorization_env_accepted=False))
    assert d.authorized is False
    assert "authorization_env_not_accepted" in d.fixed_error_codes


def test_gate_type_invalid_blocked():
    d = _evaluate(_valid_input(authorization_flag_present=1))
    assert d.authorized is False
    assert "authorization_gate_type_invalid" in d.fixed_error_codes
    assert d.authorization_context_valid is False


# ---- context / binding ----
def test_context_expired_blocked():
    ctx = _valid_context(expires_at="2026-07-01T00:00:00Z")
    d = _evaluate(_valid_input(authorization_context=ctx))
    assert d.authorized is False
    assert "authorization_context_expired" in d.fixed_error_codes


def test_repository_mismatch_blocked():
    d = _evaluate(_valid_input(expected_repository="other/repo"))
    assert d.authorized is False
    assert "authorization_binding_repository_mismatch" in d.fixed_error_codes


def test_branch_mismatch_blocked():
    d = _evaluate(_valid_input(expected_branch="master"))
    assert d.authorized is False


def test_commit_mismatch_blocked():
    d = _evaluate(_valid_input(expected_commit_sha="f" * 40))
    assert d.authorized is False


def test_manifest_mismatch_blocked():
    d = _evaluate(_valid_input(expected_manifest_sha256="f" * 64))
    assert d.authorized is False


def test_run_id_mismatch_blocked():
    d = _evaluate(_valid_input(), plan_run_id="run-" + "e" * 12)
    assert d.authorized is False
    assert "authorization_binding_run_id_mismatch" in d.fixed_error_codes


def test_loader_identity_mismatch_blocked():
    d = _evaluate(_valid_input(expected_loader_name="other-loader"))
    assert d.authorized is False


def test_adapter_identity_mismatch_blocked():
    d = _evaluate(_valid_input(expected_adapter_version="9.9.9"))
    assert d.authorized is False


def test_network_policy_version_mismatch_blocked():
    d = _evaluate(_valid_input(expected_network_policy_version="other"))
    assert d.authorized is False
    assert ("authorization_binding_network_policy_version_mismatch"
            in d.fixed_error_codes)


# ---- network policy ----
def test_network_policy_allow_network_true_blocked():
    d = _evaluate(_valid_input(
        network_policy=NetworkPolicyV1(allow_network=True)))
    assert d.authorized is False
    assert "network_policy_not_deny_all" in d.fixed_error_codes


def test_network_policy_destinations_nonempty_blocked():
    d = _evaluate(_valid_input(
        network_policy=NetworkPolicyV1(
            allowed_destination_ids=("api.example.com",))))
    assert d.authorized is False
    assert "network_policy_destination_not_empty" in d.fixed_error_codes


# ---- requested ----
def test_requested_network_calls_one_blocked():
    d = _evaluate(_valid_input(requested_network_calls=1))
    assert d.authorized is False
    assert "authorization_network_calls_forbidden" in d.fixed_error_codes


def test_requested_case_count_mismatch_blocked():
    d = _evaluate(_valid_input(requested_case_count=2), case_count=1)
    assert d.authorized is False
    assert "authorization_requested_case_count_mismatch" in \
        d.fixed_error_codes


def test_requested_image_count_mismatch_blocked():
    d = _evaluate(_valid_input(requested_image_count=5), image_count=1)
    assert d.authorized is False
    assert "authorization_requested_image_count_mismatch" in \
        d.fixed_error_codes


def test_budget_exceeded_blocked():
    d = _evaluate(_valid_input(requested_case_count=11))
    assert d.authorized is False
    assert "authorization_budget_case_exceeded" in d.fixed_error_codes


# ---- 決定屬性 ----
def test_context_absent_digest_empty():
    d = _evaluate(_valid_input(authorization_context=None))
    assert d.authorization_context_digest == ""


def test_errors_order_unique_deterministic():
    d1 = _evaluate(_valid_input(
        authorization_flag_present=False,
        authorization_env_present=False,
        authorization_context=None))
    d2 = _evaluate(_valid_input(
        authorization_flag_present=False,
        authorization_env_present=False,
        authorization_context=None))
    codes1 = list(d1.fixed_error_codes)
    assert codes1 == list(dict.fromkeys(codes1))
    assert codes1 == list(d2.fixed_error_codes)
    assert d1.to_dict() == d2.to_dict()


def test_all_codes_in_allowlist():
    from alkaid_cs2.evaluation.authorization_context import (
        AUTHORIZATION_ALL_ERROR_CODES,
    )
    from alkaid_cs2.evaluation.network_policy import (
        NETWORK_POLICY_ERROR_CODES,
    )
    ALL = AUTHORIZATION_ALL_ERROR_CODES | NETWORK_POLICY_ERROR_CODES
    cases = [
        _evaluate(_valid_input(authorization_context=None)),
        _evaluate(_valid_input(
            network_policy=NetworkPolicyV1(allow_network=True))),
        _evaluate(_valid_input(requested_case_count=99), case_count=1),
        _evaluate(_valid_input(requested_image_count=99), image_count=1),
        _evaluate(_valid_input(requested_network_calls=1)),
        _evaluate(_valid_input(expected_branch="x")),
    ]
    for d in cases:
        for c in d.fixed_error_codes:
            assert c in ALL, c
