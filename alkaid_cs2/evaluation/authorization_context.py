# -*- coding: utf-8 -*-
"""
authorization_context.py — AuthorizationContextV1 + AuthorizationDecision
（Phase 6.4C2-B2-A / B2-A.1 Final Authorization Hardening）

- 不可變 context（frozen dataclass）
- validate_authorization_context：固定錯誤碼、保序、唯一、deterministic
- evaluate_authorization：五 gate → AuthorizationDecision
  （authorized 由全部 gate 推導，不得由 caller 直接傳入）
- B2-A.1：gate 必須真正 bool；requested budget 必須非負 int（bool 拒）；
  now_utc 必須真正 UTC datetime；context_valid 涵蓋 binding+budget；
  error code allowlist 公開三集合
- context canonical digest：SHA-256、64 位小寫 hex、不含敏感值
"""

from __future__ import annotations

import datetime
import hashlib
import json
import re
from dataclasses import dataclass, fields

# ---- 固定常數 ----
AUTHORIZATION_SCHEMA_VERSION = "authorization-context-v1"
AUTHORIZATION_SCOPE_ALLOWLIST = frozenset({"evaluation"})
EXECUTION_MODE_ALLOWLIST = frozenset({"contract_only", "dry_run"})

_AUTH_ID_RE = re.compile(r"^auth-[0-9a-f]{16}$")
_RUN_ID_RE = re.compile(r"^run-[0-9a-f]{12}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

# ---- Error code allowlist（B2-A.1：公開三集合，decision 只回傳這些）----
AUTHORIZATION_CONTEXT_ERROR_CODES = frozenset({
    "authorization_context_missing",
    "authorization_context_invalid",
    "authorization_context_expired",
    "authorization_scope_mismatch",
    "authorization_execution_mode_invalid",
    "authorization_id_invalid",
    "authorization_run_id_invalid",
    "authorization_timestamp_invalid",
    "authorization_now_utc_invalid",
    "authorization_expiry_not_after_approved",
    "authorization_commit_sha_invalid",
    "authorization_manifest_sha_invalid",
    "authorization_config_sha_invalid",
    "authorization_budget_invalid",
    "authorization_network_budget_nonzero",
    "authorization_identity_invalid",
})

AUTHORIZATION_DECISION_ERROR_CODES = frozenset({
    "authorization_flag_missing",
    "authorization_env_missing",
    "authorization_env_not_accepted",
    "authorization_gate_type_invalid",
    "authorization_requested_budget_invalid",
    "authorization_binding_repository_mismatch",
    "authorization_binding_branch_mismatch",
    "authorization_binding_commit_sha_mismatch",
    "authorization_binding_manifest_sha256_mismatch",
    "authorization_binding_run_id_mismatch",
    "authorization_binding_loader_name_mismatch",
    "authorization_binding_loader_version_mismatch",
    "authorization_binding_adapter_name_mismatch",
    "authorization_binding_adapter_version_mismatch",
    "authorization_binding_adapter_config_sha256_mismatch",
    "authorization_binding_network_policy_version_mismatch",
    "authorization_budget_case_exceeded",
    "authorization_budget_image_exceeded",
    "authorization_budget_bytes_exceeded",
    "authorization_budget_wall_time_exceeded",
    "authorization_budget_network_exceeded",
    "authorization_network_calls_forbidden",
})

AUTHORIZATION_ALL_ERROR_CODES = (
    AUTHORIZATION_CONTEXT_ERROR_CODES | AUTHORIZATION_DECISION_ERROR_CODES)

# B2-A.3：binding 固定錯誤碼常數（不得用 f-string 組字串）
ERROR_BINDING_REPOSITORY_MISMATCH = "authorization_binding_repository_mismatch"
ERROR_BINDING_BRANCH_MISMATCH = "authorization_binding_branch_mismatch"
ERROR_BINDING_COMMIT_SHA_MISMATCH = "authorization_binding_commit_sha_mismatch"
ERROR_BINDING_MANIFEST_SHA256_MISMATCH = "authorization_binding_manifest_sha256_mismatch"
ERROR_BINDING_RUN_ID_MISMATCH = "authorization_binding_run_id_mismatch"
ERROR_BINDING_LOADER_NAME_MISMATCH = "authorization_binding_loader_name_mismatch"
ERROR_BINDING_LOADER_VERSION_MISMATCH = "authorization_binding_loader_version_mismatch"
ERROR_BINDING_ADAPTER_NAME_MISMATCH = "authorization_binding_adapter_name_mismatch"
ERROR_BINDING_ADAPTER_VERSION_MISMATCH = "authorization_binding_adapter_version_mismatch"
ERROR_BINDING_ADAPTER_CONFIG_SHA256_MISMATCH = "authorization_binding_adapter_config_sha256_mismatch"
ERROR_BINDING_NETWORK_POLICY_VERSION_MISMATCH = "authorization_binding_network_policy_version_mismatch"



def _parse_utc(value: object) -> datetime.datetime | None:
    """真正 datetime 解析（非 regex only；拒 2026-99-99、25:61）。"""
    if not isinstance(value, str) or not _UTC_TS_RE.match(value):
        return None
    try:
        return datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None


def _is_valid_utc_timestamp(value: object) -> bool:
    return _parse_utc(value) is not None


def _is_positive_int(value: object) -> bool:
    """int 且 > 0；bool 不得視為 int。"""
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _is_nonneg_int(value: object) -> bool:
    """int 且 >= 0；bool 不得視為 int。"""
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


@dataclass(frozen=True)
class AuthorizationContextV1:
    """B2-A 授權 context（immutable）。

    格式契約：
    - authorization_id: auth-[0-9a-f]{16}
    - approved_run_id: run-[0-9a-f]{12}
    - commit_sha: 40 位 lowercase hex
    - dataset_manifest_sha256 / adapter_config_sha256: 64 位 lowercase hex
    - approved_at / expires_at: 真實 UTC（datetime.strptime）
    - budget 全為 int（bool 拒）、> 0；max_network_calls 必須為 0（B2-A）
    """

    schema_version: str = AUTHORIZATION_SCHEMA_VERSION
    authorization_id: str = ""
    authorization_scope: str = "evaluation"
    approved_at: str = ""
    expires_at: str = ""
    repository: str = ""
    branch: str = ""
    commit_sha: str = ""
    dataset_manifest_sha256: str = ""
    execution_mode: str = "contract_only"
    approved_run_id: str = ""
    loader_name: str = ""
    loader_version: str = ""
    adapter_name: str = ""
    adapter_version: str = ""
    adapter_config_sha256: str = ""
    network_policy_version: str = ""
    max_case_count: int = 0
    max_image_count: int = 0
    max_network_calls: int = 0
    max_total_image_bytes: int = 0
    max_wall_time_seconds: int = 0


def validate_authorization_context(
    context: AuthorizationContextV1 | None,
    *,
    now_utc: str,
) -> list[str]:
    """驗證 context；回傳固定錯誤碼（保序、唯一、無動態值）。

    B2-A.1：
    - now_utc 必須真正 UTC datetime（strptime）；無效 → 
      authorization_now_utc_invalid，且不得進行 expiry comparison、
      不得誤報 authorization_context_expired
    - approved_at/expires_at/now_utc parse 成 datetime 後比較
    """
    errors: list[str] = []
    now_dt = _parse_utc(now_utc)
    if now_dt is None:
        errors.append("authorization_now_utc_invalid")
    if context is None:
        return list(dict.fromkeys(errors)) or [
            "authorization_context_missing"]
    if not isinstance(context, AuthorizationContextV1):
        return list(dict.fromkeys(errors)) or ["authorization_context_invalid"]
    if context.schema_version != AUTHORIZATION_SCHEMA_VERSION:
        errors.append("authorization_context_invalid")
    if not _AUTH_ID_RE.match(context.authorization_id or ""):
        errors.append("authorization_id_invalid")
    if context.authorization_scope not in AUTHORIZATION_SCOPE_ALLOWLIST:
        errors.append("authorization_scope_mismatch")
    if context.execution_mode not in EXECUTION_MODE_ALLOWLIST:
        errors.append("authorization_execution_mode_invalid")
    approved_dt = _parse_utc(context.approved_at)
    expires_dt = _parse_utc(context.expires_at)
    if approved_dt is None:
        errors.append("authorization_timestamp_invalid")
    if expires_dt is None:
        errors.append("authorization_timestamp_invalid")
    if approved_dt is not None and expires_dt is not None:
        if expires_dt <= approved_dt:
            errors.append("authorization_expiry_not_after_approved")
        if now_dt is not None and expires_dt <= now_dt:
            errors.append("authorization_context_expired")
    if not _SHA1_RE.match(context.commit_sha or ""):
        errors.append("authorization_commit_sha_invalid")
    if not _SHA256_RE.match(context.dataset_manifest_sha256 or ""):
        errors.append("authorization_manifest_sha_invalid")
    if not _SHA256_RE.match(context.adapter_config_sha256 or ""):
        errors.append("authorization_config_sha_invalid")
    if not _RUN_ID_RE.match(context.approved_run_id or ""):
        errors.append("authorization_run_id_invalid")
    if not context.repository or not context.branch \
            or not context.loader_name or not context.loader_version \
            or not context.adapter_name or not context.adapter_version \
            or not context.network_policy_version:
        errors.append("authorization_identity_invalid")
    for budget_field in ("max_case_count", "max_image_count",
                         "max_total_image_bytes", "max_wall_time_seconds"):
        v = getattr(context, budget_field)
        if not _is_positive_int(v):
            errors.append("authorization_budget_invalid")
    mc = context.max_network_calls
    if not isinstance(mc, int) or isinstance(mc, bool):
        errors.append("authorization_budget_invalid")
    elif mc != 0:
        errors.append("authorization_network_budget_nonzero")
    return list(dict.fromkeys(errors))


def authorization_context_to_dict(context: AuthorizationContextV1) -> dict:
    """受控 dictionary（不含敏感值；欄位固定 allowlist）。"""
    return {f.name: getattr(context, f.name) for f in fields(context)}


def compute_authorization_context_digest(
    context: AuthorizationContextV1,
) -> str:
    """canonical digest：json.dumps(sort_keys, separators) → SHA-256。"""
    canonical = json.dumps(
        authorization_context_to_dict(context),
        sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class AuthorizationDecision:
    """evaluate_authorization 的結果（immutable；gate 欄位全是真正 bool）。"""

    authorization_flag_present: bool
    authorization_env_present: bool
    authorization_env_accepted: bool
    authorization_context_present: bool
    authorization_context_valid: bool
    authorized: bool
    authorization_context_digest: str
    fixed_error_codes: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        return {
            "authorization_flag_present": self.authorization_flag_present,
            "authorization_env_present": self.authorization_env_present,
            "authorization_env_accepted": self.authorization_env_accepted,
            "authorization_context_present": self.authorization_context_present,
            "authorization_context_valid": self.authorization_context_valid,
            "authorized": self.authorized,
            "authorization_context_digest": self.authorization_context_digest,
            "fixed_error_codes": list(self.fixed_error_codes),
        }


def _require_bool(value: object, code: str, errors: list[str]) -> bool:
    """B2-A.1：gate 必須真正 bool（拒 0/1/"true"/None/[]/{}）。"""
    if not isinstance(value, bool):
        errors.append(code)
        return False
    return True


def _validate_requested_budgets(
    *,
    requested_case_count: object,
    requested_image_count: object,
    requested_network_calls: object,
    requested_total_image_bytes: object,
    requested_wall_time_seconds: object,
) -> list[str]:
    """B2-A.1：requested 值必須非負 int（bool 拒）。

    任一不合法 → authorization_requested_budget_invalid。
    """
    invalid = [
        not _is_nonneg_int(v) for v in (
            requested_case_count, requested_image_count,
            requested_network_calls, requested_total_image_bytes,
            requested_wall_time_seconds)]
    return ["authorization_requested_budget_invalid"] if any(invalid) else []


def evaluate_authorization(
    *,
    authorization_flag_present: object,
    authorization_env_present: object,
    authorization_env_accepted: object,
    authorization_context: AuthorizationContextV1 | None,
    expected_repository: str,
    expected_branch: str,
    expected_commit_sha: str,
    expected_manifest_sha256: str,
    expected_run_id: str,
    expected_loader_name: str,
    expected_loader_version: str,
    expected_adapter_name: str,
    expected_adapter_version: str,
    expected_adapter_config_sha256: str,
    expected_network_policy_version: str,
    requested_case_count: object,
    requested_image_count: object,
    requested_network_calls: object,
    requested_total_image_bytes: object,
    requested_wall_time_seconds: object,
    now_utc: str,
) -> AuthorizationDecision:
    """五 gate 授權判定（B2-A.1）。

    - gate 型別錯誤 → authorization_gate_type_invalid + authorized=False
    - requested budget 型別錯誤 → authorization_requested_budget_invalid
      + context_valid=False
    - context_valid 涵蓋 16 條件（schema/expiry/binding/budget/network==0）
    - flag/env missing/rejected 不影響 context_valid（只影響 authorized）
    - authorized = 所有 gate bool 合法 AND flag AND env present AND env
      accepted AND context_valid
    - 不得讓 caller 傳 authorized=True；不得信任 plan.authorized
    """
    errors: list[str] = []
    gate_ok = True
    for value in (authorization_flag_present, authorization_env_present,
                  authorization_env_accepted):
        if not _require_bool(value, "authorization_gate_type_invalid",
                             errors):
            gate_ok = False

    flag_present = authorization_flag_present if isinstance(
        authorization_flag_present, bool) else False
    env_present = authorization_env_present if isinstance(
        authorization_env_present, bool) else False
    env_accepted = authorization_env_accepted if isinstance(
        authorization_env_accepted, bool) else False

    ctx_errors = validate_authorization_context(
        authorization_context, now_utc=now_utc)
    context_present = isinstance(authorization_context,
                                 AuthorizationContextV1)

    # requested budget 型別驗證（型別錯誤 → context_valid=False）
    budget_errors = _validate_requested_budgets(
        requested_case_count=requested_case_count,
        requested_image_count=requested_image_count,
        requested_network_calls=requested_network_calls,
        requested_total_image_bytes=requested_total_image_bytes,
        requested_wall_time_seconds=requested_wall_time_seconds)
    if budget_errors:
        errors.extend(budget_errors)

    # flag/env gate（型別合法才加 missing/not-accepted）
    if isinstance(authorization_flag_present, bool) \
            and not authorization_flag_present:
        errors.append("authorization_flag_missing")
    if isinstance(authorization_env_present, bool) \
            and not authorization_env_present:
        errors.append("authorization_env_missing")
    if isinstance(authorization_env_accepted, bool) \
            and not authorization_env_accepted:
        errors.append("authorization_env_not_accepted")

    if not context_present and "authorization_context_missing" \
            not in ctx_errors:
        ctx_errors = list(ctx_errors) + ["authorization_context_missing"]

    # identity binding + budget（只在 context 結構合法且型別都過時做）
    binding_errors: list[str] = []
    if context_present and not ctx_errors and not budget_errors:
        ctx = authorization_context
        bindings = [
            (ctx.repository, expected_repository,
             ERROR_BINDING_REPOSITORY_MISMATCH),
            (ctx.branch, expected_branch, ERROR_BINDING_BRANCH_MISMATCH),
            (ctx.commit_sha, expected_commit_sha,
             ERROR_BINDING_COMMIT_SHA_MISMATCH),
            (ctx.dataset_manifest_sha256, expected_manifest_sha256,
             ERROR_BINDING_MANIFEST_SHA256_MISMATCH),
            (ctx.approved_run_id, expected_run_id,
             ERROR_BINDING_RUN_ID_MISMATCH),
            (ctx.loader_name, expected_loader_name,
             ERROR_BINDING_LOADER_NAME_MISMATCH),
            (ctx.loader_version, expected_loader_version,
             ERROR_BINDING_LOADER_VERSION_MISMATCH),
            (ctx.adapter_name, expected_adapter_name,
             ERROR_BINDING_ADAPTER_NAME_MISMATCH),
            (ctx.adapter_version, expected_adapter_version,
             ERROR_BINDING_ADAPTER_VERSION_MISMATCH),
            (ctx.adapter_config_sha256, expected_adapter_config_sha256,
             ERROR_BINDING_ADAPTER_CONFIG_SHA256_MISMATCH),
            (ctx.network_policy_version, expected_network_policy_version,
             ERROR_BINDING_NETWORK_POLICY_VERSION_MISMATCH),
        ]
        for actual, expected, code in bindings:
            if actual != expected:
                binding_errors.append(code)
        if requested_network_calls > ctx.max_network_calls:
            binding_errors.append("authorization_budget_network_exceeded")
        if requested_case_count > ctx.max_case_count:
            binding_errors.append("authorization_budget_case_exceeded")
        if requested_image_count > ctx.max_image_count:
            binding_errors.append("authorization_budget_image_exceeded")
        if requested_total_image_bytes > ctx.max_total_image_bytes:
            binding_errors.append("authorization_budget_bytes_exceeded")
        if requested_wall_time_seconds > ctx.max_wall_time_seconds:
            binding_errors.append("authorization_budget_wall_time_exceeded")
        if requested_network_calls != 0:
            binding_errors.append("authorization_network_calls_forbidden")

    all_errors = errors + ctx_errors + binding_errors
    # B2-A.2：context_valid=True 必須同時要求 gate 型別全合法
    # （gate 值 False ≠ gate 型別錯誤：值 False 不影響 context_valid，
    #   型別錯誤使 context_valid=False）
    context_valid = (
        gate_ok
        and context_present
        and not ctx_errors
        and not budget_errors
        and not binding_errors)

    digest = ""
    if context_present:
        digest = compute_authorization_context_digest(authorization_context)
    authorized = (
        gate_ok and flag_present and env_present and env_accepted
        and context_valid)
    return AuthorizationDecision(
        authorization_flag_present=flag_present,
        authorization_env_present=env_present,
        authorization_env_accepted=env_accepted,
        authorization_context_present=context_present,
        authorization_context_valid=context_valid,
        authorized=authorized,
        authorization_context_digest=digest,
        fixed_error_codes=tuple(dict.fromkeys(all_errors)),
    )
