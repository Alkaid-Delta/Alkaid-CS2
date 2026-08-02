# -*- coding: utf-8 -*-
"""
network_policy.py — NetworkPolicyV1 deny-all（Phase 6.4C2-B2-A）

- B2-A 唯一合法模式：deny_all（allow_network=False、全部 IP 類別禁、
  budgets 全 0、destinations 空 tuple）
- validate_network_policy：固定錯誤碼、保序、唯一
- assert_network_disabled：deny-all 不成立時回傳錯誤碼
- 不得建立 socket / DNS / import HTTP SDK / 讀 proxy 或 endpoint env
"""

from __future__ import annotations

import re
from dataclasses import dataclass

NETWORK_POLICY_SCHEMA_VERSION = "network-policy-v1"
NETWORK_POLICY_DENY_ALL_MODE = "deny_all"

# 錯誤碼 allowlist
NETWORK_POLICY_ERROR_CODES = frozenset({
    "network_policy_invalid",
    "network_policy_not_deny_all",
    "network_policy_destination_not_empty",
    "network_policy_redirects_enabled",
    "network_policy_proxy_enabled",
    "network_policy_private_ip_enabled",
    "network_policy_loopback_enabled",
    "network_policy_link_local_enabled",
    "network_policy_metadata_ip_enabled",
    "network_policy_call_budget_nonzero",
    "network_policy_concurrency_nonzero",
})

_SAFE_STRING_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


@dataclass(frozen=True)
class NetworkPolicyV1:
    """B2-A deny-all network policy（immutable；destinations 用 tuple）。"""

    schema_version: str = NETWORK_POLICY_SCHEMA_VERSION
    policy_version: str = "deny-all-1"
    mode: str = NETWORK_POLICY_DENY_ALL_MODE
    allow_network: bool = False
    allowed_destination_ids: tuple[str, ...] = ()
    allow_redirects: bool = False
    allow_proxy_env: bool = False
    allow_private_ip: bool = False
    allow_loopback: bool = False
    allow_link_local: bool = False
    allow_metadata_ip: bool = False
    max_network_calls: int = 0
    max_concurrency: int = 0
    connect_timeout_seconds: int = 0
    response_timeout_seconds: int = 0
    max_request_bytes: int = 0
    max_response_bytes: int = 0


def _is_bool(value: object) -> bool:
    return isinstance(value, bool)


def _is_zero_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value == 0


def validate_network_policy(policy: NetworkPolicyV1 | None) -> list[str]:
    """結構/格式驗證；回傳固定錯誤碼（保序、唯一）。"""
    errors: list[str] = []
    if policy is None:
        return ["network_policy_invalid"]
    if not isinstance(policy, NetworkPolicyV1):
        return ["network_policy_invalid"]
    if policy.schema_version != NETWORK_POLICY_SCHEMA_VERSION:
        errors.append("network_policy_invalid")
    if not _SAFE_STRING_RE.match(policy.policy_version or ""):
        errors.append("network_policy_invalid")
    if policy.mode != NETWORK_POLICY_DENY_ALL_MODE:
        errors.append("network_policy_not_deny_all")
    if not isinstance(policy.allow_network, bool):
        errors.append("network_policy_invalid")
    elif policy.allow_network is True:
        errors.append("network_policy_not_deny_all")
    if not isinstance(policy.allowed_destination_ids, tuple):
        errors.append("network_policy_invalid")
    elif policy.allowed_destination_ids:
        errors.append("network_policy_destination_not_empty")
    for flag, code in [
        ("allow_redirects", "network_policy_redirects_enabled"),
        ("allow_proxy_env", "network_policy_proxy_enabled"),
        ("allow_private_ip", "network_policy_private_ip_enabled"),
        ("allow_loopback", "network_policy_loopback_enabled"),
        ("allow_link_local", "network_policy_link_local_enabled"),
        ("allow_metadata_ip", "network_policy_metadata_ip_enabled"),
    ]:
        v = getattr(policy, flag)
        if not isinstance(v, bool):
            errors.append("network_policy_invalid")
        elif v is True:
            errors.append(code)
    for field in ("max_network_calls", "max_concurrency"):
        v = getattr(policy, field)
        if not _is_zero_int(v):
            errors.append(
                "network_policy_call_budget_nonzero"
                if field == "max_network_calls"
                else "network_policy_concurrency_nonzero")
    for field in ("connect_timeout_seconds", "response_timeout_seconds",
                  "max_request_bytes", "max_response_bytes"):
        v = getattr(policy, field)
        if not _is_zero_int(v):
            errors.append("network_policy_invalid")
    return list(dict.fromkeys(errors))


def assert_network_disabled(policy: NetworkPolicyV1 | None) -> list[str]:
    """deny-all 不成立 → 回傳錯誤碼（保序、唯一）。"""
    errors = validate_network_policy(policy)
    if not errors:
        return []
    # 只保留「enablement」類錯誤（deny-all 違反）；結構錯誤保留 invalid
    enabled_codes = {
        "network_policy_not_deny_all",
        "network_policy_destination_not_empty",
        "network_policy_redirects_enabled",
        "network_policy_proxy_enabled",
        "network_policy_private_ip_enabled",
        "network_policy_loopback_enabled",
        "network_policy_link_local_enabled",
        "network_policy_metadata_ip_enabled",
        "network_policy_call_budget_nonzero",
        "network_policy_concurrency_nonzero",
    }
    return [e for e in errors if e in enabled_codes or e == "network_policy_invalid"]
