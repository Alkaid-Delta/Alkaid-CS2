# -*- coding: utf-8 -*-
"""test_network_policy.py — Phase 6.4C2-B2-A NetworkPolicyV1 deny-all"""
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)

import pytest  # noqa: E402

from alkaid_cs2.evaluation.network_policy import (  # noqa: E402
    NETWORK_POLICY_SCHEMA_VERSION,
    NetworkPolicyV1,
    assert_network_disabled,
    validate_network_policy,
)


def _policy(**over):
    p = dict(
        schema_version=NETWORK_POLICY_SCHEMA_VERSION,
        policy_version="deny-all-1",
        mode="deny_all",
        allow_network=False,
        allowed_destination_ids=(),
        allow_redirects=False,
        allow_proxy_env=False,
        allow_private_ip=False,
        allow_loopback=False,
        allow_link_local=False,
        allow_metadata_ip=False,
        max_network_calls=0,
        max_concurrency=0,
        connect_timeout_seconds=0,
        response_timeout_seconds=0,
        max_request_bytes=0,
        max_response_bytes=0,
    )
    p.update(over)
    return NetworkPolicyV1(**p)


def test_valid_deny_all_accepted():
    assert validate_network_policy(_policy()) == []
    assert assert_network_disabled(_policy()) == []


def test_allow_network_true_rejected():
    errs = validate_network_policy(_policy(allow_network=True))
    assert "network_policy_not_deny_all" in errs


def test_destinations_non_empty_rejected():
    errs = validate_network_policy(
        _policy(allowed_destination_ids=("api.example.com",)))
    assert "network_policy_destination_not_empty" in errs


def test_redirects_enabled_rejected():
    errs = validate_network_policy(_policy(allow_redirects=True))
    assert "network_policy_redirects_enabled" in errs


def test_proxy_enabled_rejected():
    errs = validate_network_policy(_policy(allow_proxy_env=True))
    assert "network_policy_proxy_enabled" in errs


def test_private_ip_enabled_rejected():
    errs = validate_network_policy(_policy(allow_private_ip=True))
    assert "network_policy_private_ip_enabled" in errs


def test_loopback_enabled_rejected():
    errs = validate_network_policy(_policy(allow_loopback=True))
    assert "network_policy_loopback_enabled" in errs


def test_link_local_enabled_rejected():
    errs = validate_network_policy(_policy(allow_link_local=True))
    assert "network_policy_link_local_enabled" in errs


def test_metadata_ip_enabled_rejected():
    errs = validate_network_policy(_policy(allow_metadata_ip=True))
    assert "network_policy_metadata_ip_enabled" in errs


def test_max_calls_nonzero_rejected():
    errs = validate_network_policy(_policy(max_network_calls=1))
    assert "network_policy_call_budget_nonzero" in errs


def test_max_concurrency_nonzero_rejected():
    errs = validate_network_policy(_policy(max_concurrency=1))
    assert "network_policy_concurrency_nonzero" in errs


def test_bool_as_int_rejected():
    errs = validate_network_policy(_policy(max_network_calls=True))
    assert "network_policy_call_budget_nonzero" in errs or \
        "network_policy_invalid" in errs


def test_unknown_schema_rejected():
    errs = validate_network_policy(_policy(schema_version="network-policy-v0"))
    assert "network_policy_invalid" in errs


def test_mutable_list_destinations_rejected():
    # destinations 用 mutable list → validate 拒絕（必須 immutable tuple）
    p = NetworkPolicyV1(
        **{k: v for k, v in _policy().__dict__.items()})
    object.__setattr__(p, "allowed_destination_ids", ["a"])
    errs = validate_network_policy(p)
    assert "network_policy_invalid" in errs


def test_assert_network_disabled_returns_enablement_codes():
    p = _policy(allow_redirects=True, max_concurrency=1)
    errs = assert_network_disabled(p)
    assert "network_policy_redirects_enabled" in errs
    assert "network_policy_concurrency_nonzero" in errs


def test_policy_none_invalid():
    assert validate_network_policy(None) == ["network_policy_invalid"]


def test_error_codes_unique_and_ordered():
    # B2-A.1：明確 expected list（非自我參照排序）
    p = _policy(allow_network=True, allow_redirects=True,
                allow_proxy_env=True, allow_private_ip=True,
                allow_loopback=True, allow_link_local=True,
                allow_metadata_ip=True, max_network_calls=1,
                max_concurrency=1)
    errs = validate_network_policy(p)
    assert errs == [
        "network_policy_not_deny_all",
        "network_policy_redirects_enabled",
        "network_policy_proxy_enabled",
        "network_policy_private_ip_enabled",
        "network_policy_loopback_enabled",
        "network_policy_link_local_enabled",
        "network_policy_metadata_ip_enabled",
        "network_policy_call_budget_nonzero",
        "network_policy_concurrency_nonzero",
    ], errs
