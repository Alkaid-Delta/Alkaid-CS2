# -*- coding: utf-8 -*-
"""
external_analyzer_runner.py — Secure external analyzer execution harness（Phase 6.4C2-B0）

- 預設 external_analyzer_enabled = False
- 必須同時具備 CLI flag + env 才可進入 analyzer execution path
- no-real-data safe stop：anonymized_real=0 → blocked（不載入 bytes、不建 network）
- 本階段只提供 dry-run 與 gate/plan（不實際呼叫 analyzer）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from alkaid_cs2.evaluation.analyzer_audit import AUDIT_SCHEMA_VERSION
from alkaid_cs2.evaluation.analyzer_cache import (
    CACHE_SCHEMA_VERSION,
    compute_opaque_case_key,
)
from alkaid_cs2.evaluation.intake_validation import (
    validate_secure_store_reference,
)

PLAN_SCHEMA_VERSION = "external-analyzer-plan-v1"

# 固定錯誤碼（Phase 6.4C2-B0）
ERROR_FLAG_MISSING = "external_analyzer_flag_missing"
ERROR_ENV_MISSING = "external_analyzer_env_missing"
ERROR_NOT_AUTHORIZED = "external_analyzer_not_authorized"
ERROR_NO_REAL_CASES = "no_eligible_real_cases"
ERROR_NO_ELIGIBLE_IMAGES = "no_eligible_real_images"
ERROR_LOADER_UNAVAILABLE = "secure_image_loader_unavailable"
ERROR_ADAPTER_UNAVAILABLE = "analyzer_adapter_unavailable"
ERROR_REFERENCE_INVALID = "secure_reference_invalid"
ERROR_IMAGE_NOT_FOUND = "secure_image_not_found"
ERROR_HASH_MISMATCH = "secure_image_hash_mismatch"
ERROR_ADAPTER_FAILED = "analyzer_execution_failed"
ERROR_RESULT_INVALID = "analyzer_result_invalid"
ERROR_CACHE_WRITE = "cache_write_failed"
ERROR_AUDIT_WRITE = "audit_write_failed"
ERROR_MANIFEST_MISSING = "manifest_missing"
ERROR_MANIFEST_INVALID_JSON = "manifest_invalid_json"
ERROR_MANIFEST_VALIDATION = "manifest_validation_failed"
ERROR_OUTPUT_PATH = "output_path_not_allowed"
# Phase 6.4C2-B0.2：fixture integrity 固定碼
ERROR_FIXTURE_MISSING = "manifest_case_fixture_missing"
ERROR_FIXTURE_INVALID = "manifest_case_fixture_invalid"
ERROR_FIXTURE_HASH_MISMATCH = "manifest_fixture_hash_mismatch"
ERROR_FIXTURE_CASE_ID_MISMATCH = "manifest_fixture_case_id_mismatch"
ERROR_FIXTURE_SOURCE_MISMATCH = "manifest_fixture_source_mismatch"
ERROR_IMAGE_HASH_INVALID = "manifest_image_hash_invalid"
ERROR_IMAGE_COUNT_MISMATCH = "manifest_image_count_mismatch"
ERROR_INVALID_STORAGE_REF = "manifest_invalid_storage_reference"
ERROR_FIXTURE_PRIVACY = "manifest_fixture_privacy_failed"


@dataclass
class EligibleAnalyzerCase:
    """由 manifest 產生的 eligible case（僅記憶體）。

    - source_case_id：僅記憶體內使用的原 case id（不寫入 plan/audit/cache）
    - storage_references / image_hashes：一一對應
    """

    source_case_id: str
    storage_references: list[str]
    image_hashes: list[str]
    review_status: str
    privacy_scan_status: str
    source: str

    def as_dict(self) -> dict:
        return {
            "case_id": self.source_case_id,
            "storage_reference": (self.storage_references[0]
                                  if self.storage_references else ""),
            "image_hashes": self.image_hashes,
        }


# review status 可進入 readiness（與 can_enter_real_readiness 一致）
READINESS_REVIEW_STATUSES = frozenset({"double_review"})


def load_eligible_cases_from_manifest(
    manifest_path: str | os.PathLike[str],
    fixtures_dir: str | os.PathLike[str],
) -> tuple[list[EligibleAnalyzerCase], list[str]]:
    """由 evaluation_real manifest + fixtures 產生 eligible cases。

    - manifest 缺失 → manifest_missing
    - manifest JSON 壞掉 → manifest_invalid_json
    - validate_real_manifest 失敗 → manifest_validation_failed
    - 只收 source=anonymized_real、privacy passed、double_review
    - image reference count 與 hash 數一致
    - storage references 必須合法 secure-store URI
    - 不讀圖片 bytes、不讀 production secret、不回顯值
    """
    from alkaid_cs2.evaluation.intake_models import validate_real_manifest
    errors: list[str] = []
    mp = Path(manifest_path)
    if not mp.exists():
        return [], [ERROR_MANIFEST_MISSING]
    try:
        manifest = json.loads(mp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return [], [ERROR_MANIFEST_INVALID_JSON]
    ok, reasons = validate_real_manifest(manifest, None)
    if not ok:
        return [], [ERROR_MANIFEST_VALIDATION]
    eligible: list[EligibleAnalyzerCase] = []
    fixtures_dir_path = Path(fixtures_dir)
    for entry in manifest.get("cases", []):
        if entry.get("source") != "anonymized_real":
            continue
        if entry.get("privacy_scan_status") != "passed":
            continue
        if entry.get("review_status") not in READINESS_REVIEW_STATUSES:
            continue
        case_id = entry.get("case_id", "")
        # ── Phase 6.4C2-B0.2：fixture 完整性綁定 ──
        # image 資料（hashes + secure refs）從 fixtures 的 case JSON 讀；
        # 驗證 raw bytes SHA-256 == manifest fixture_sha256
        case_file = fixtures_dir_path / f"{case_id}.json"
        if not case_file.exists():
            return [], [ERROR_FIXTURE_MISSING]
        try:
            raw = case_file.read_bytes()
            case_data = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError, OSError):
            return [], [ERROR_FIXTURE_INVALID]
        import hashlib as _hashlib
        actual_sha = _hashlib.sha256(raw).hexdigest()
        expected_sha = entry.get("fixture_sha256", "")
        if not isinstance(expected_sha, str) or actual_sha != expected_sha:
            return [], [ERROR_FIXTURE_HASH_MISMATCH]
        if case_data.get("case_id") != case_id:
            return [], [ERROR_FIXTURE_CASE_ID_MISMATCH]
        if case_data.get("source") != "anonymized_real":
            return [], [ERROR_FIXTURE_SOURCE_MISMATCH]
        hashes = case_data.get("original_image_hashes") or []
        if not all(isinstance(h, str) and len(h) == 64 and
                   all(c in "0123456789abcdef" for c in h) for h in hashes):
            return [], [ERROR_IMAGE_HASH_INVALID]
        refs = case_data.get("storage_references") or []
        if not refs and isinstance(case_data.get(
                "original_storage_reference"), str):
            refs = [case_data["original_storage_reference"]]
        image_count = entry.get("image_reference_count", 0)
        if len(refs) != len(hashes) or len(refs) != image_count:
            return [], [ERROR_IMAGE_COUNT_MISMATCH]
        bad = [r for r in refs if not validate_secure_store_reference(r)]
        if bad:
            return [], [ERROR_INVALID_STORAGE_REF]
        # fixture privacy：不含 bytes/base64/http/private fields
        from alkaid_cs2.evaluation.intake_validation import (
            scan_redaction_issues,
        )
        privacy_errs = [f for f in scan_redaction_issues(case_data)
                        if f.severity == "error"]
        if privacy_errs:
            return [], [ERROR_FIXTURE_PRIVACY]
        eligible.append(EligibleAnalyzerCase(
            source_case_id=case_id,
            storage_references=list(refs),
            image_hashes=list(hashes),
            review_status=entry.get("review_status", ""),
            privacy_scan_status=entry.get("privacy_scan_status", ""),
            source=entry.get("source", ""),
        ))
    if errors:
        return [], errors
    return eligible, []


def can_run_external_analyzer(
    *,
    cli_allowed: bool,
    env_allowed: bool,
    anonymized_real_case_count: int,
    eligible_real_image_count: int,
    secure_loader_available: bool,
    adapter_available: bool,
) -> tuple[bool, list[str]]:
    """外部 analyzer 授權 gate（全部條件必須成立）。

    目前 baseline（anonymized_real=0、eligible images=0）→ False。
    """
    reasons: list[str] = []
    if not cli_allowed:
        reasons.append(ERROR_FLAG_MISSING)
    if not env_allowed:
        reasons.append(ERROR_ENV_MISSING)
    if anonymized_real_case_count <= 0:
        reasons.append(ERROR_NO_REAL_CASES)
    if eligible_real_image_count <= 0:
        reasons.append(ERROR_NO_ELIGIBLE_IMAGES)
    if not secure_loader_available:
        reasons.append(ERROR_LOADER_UNAVAILABLE)
    if not adapter_available:
        reasons.append(ERROR_ADAPTER_UNAVAILABLE)
    if reasons:
        reasons.append(ERROR_NOT_AUTHORIZED)
        return False, reasons
    return True, []


@dataclass
class ExternalAnalyzerExecutionPlan:
    """deterministic execution plan（opaque case keys，不存原 case ID）。"""

    run_id: str
    created_at: str
    case_count: int
    image_count: int
    case_keys: list[str] = field(default_factory=list)
    image_indexes: list[int] = field(default_factory=list)
    expected_hashes: list[str] = field(default_factory=list)
    adapter_name: str = ""
    cache_namespace: str = ""
    dry_run: bool = True
    authorized: bool = False
    status: str = "blocked"
    schema_version: str = PLAN_SCHEMA_VERSION

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "run_id": self.run_id,
            "created_at": self.created_at,
            "case_count": self.case_count,
            "image_count": self.image_count,
            "case_keys": self.case_keys,
            "image_indexes": self.image_indexes,
            "expected_hashes": self.expected_hashes,
            "adapter_name": self.adapter_name,
            "cache_namespace": self.cache_namespace,
            "dry_run": self.dry_run,
            "authorized": self.authorized,
            "status": self.status,
        }


def build_execution_plan(
    *,
    eligible_cases: list[dict],
    run_salt: str,
    adapter_name: str,
    dry_run: bool,
    authorized: bool,
    created_at: str,
) -> ExternalAnalyzerExecutionPlan:
    """由 eligible cases（每筆：case_id + storage_reference + image hashes）建 plan。

    - case_keys = sha256(case_id + run_salt)（不可逆 opaque key）
    - 不保存原 case ID / storage reference / 圖片路徑 / bytes
    """
    run_id = f"run-{uuid.uuid4().hex[:12]}"
    plan = ExternalAnalyzerExecutionPlan(
        run_id=run_id, created_at=created_at,
        case_count=len(eligible_cases),
        image_count=sum(len(c["image_hashes"]) for c in eligible_cases),
        adapter_name=adapter_name,
        # Phase 6.4C2-B1.2：cache_namespace 固定契約 = run_id 的 12-hex 部分；
        # 不參與 cache identity、不含敏感值
        cache_namespace=f"namespace-{run_id[4:]}",
        dry_run=dry_run, authorized=authorized,
    )
    for case in eligible_cases:
        case_key = compute_opaque_case_key(case["case_id"], run_salt)
        for idx, h in enumerate(case["image_hashes"]):
            plan.case_keys.append(case_key)
            plan.image_indexes.append(idx)
            plan.expected_hashes.append(h)
    plan.status = "planned" if authorized else "blocked"
    return plan


def _now_utc() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def resolve_local_data_subdir(
    path: str | os.PathLike[str],
    allowed_root: str | os.PathLike[str],
) -> str:
    """確認 path 是 allowed_root（local_data）的子路徑。

    - resolve 後必須是 local_data 的子路徑
    - 不接受 repository root / tests/ / .. traversal / 外部絕對路徑
    - 非法 → 拋 ValueError（固定碼 output_path_not_allowed）
    """
    try:
        p = Path(path).resolve()
        root = Path(allowed_root).resolve()
        p.relative_to(root)
    except (ValueError, OSError):
        raise ValueError("output_path_not_allowed") from None
    return str(p)


def run_dry_plan(
    *,
    cli_allowed: bool,
    env_allowed: bool,
    eligible_cases: list[dict],
    run_salt: str,
    adapter_name: str,
    cache_dir: str | os.PathLike[str],
    audit_dir: str | os.PathLike[str],
    loader_factory: Any | None = None,
    adapter_factory: Any | None = None,
) -> tuple[int, list[str], ExternalAnalyzerExecutionPlan]:
    """dry-run：驗證 gate、建 plan、寫 audit（Git 外）；不載入 bytes。

    - loader_factory / adapter_factory：受控依賴（dry-run 不得呼叫任何 factory）
    - 回傳 (exit_code, fixed_error_codes, plan)

    exit code：0 = 合法 dry-run 且有可執行案例；2 = gate/no-data failure；
    1 = 非預期 workflow failure。
    """
    errors: list[str] = []
    ok, reasons = can_run_external_analyzer(
        cli_allowed=cli_allowed, env_allowed=env_allowed,
        anonymized_real_case_count=len(eligible_cases),
        eligible_real_image_count=sum(
            len(c["image_hashes"]) for c in eligible_cases),
        secure_loader_available=True,
        adapter_available=True,
    )
    if not ok:
        errors = reasons
    # Phase 6.4C2-B0.1：dry-run 不得呼叫 loader/adapter factory
    created_at = _now_utc()
    plan = build_execution_plan(
        eligible_cases=eligible_cases, run_salt=run_salt,
        adapter_name=adapter_name, dry_run=True, authorized=ok,
        created_at=created_at,
    )
    # 寫 audit（Git 外）；失敗 → exit 1（audit 失敗是 workflow failure）
    try:
        from alkaid_cs2.evaluation.analyzer_audit import (
            hash_image_hashes, write_audit_manifest,
        )
        run_dir = Path(audit_dir) / plan.run_id
        audit = {
            "schema_version": AUDIT_SCHEMA_VERSION,
            "run_id": plan.run_id,
            "started_at": created_at,
            "completed_at": _now_utc(),
            "dry_run": True,
            "authorization_flag_present": cli_allowed,
            "authorization_env_present": env_allowed,
            "eligible_case_count": plan.case_count,
            "eligible_image_count": plan.image_count,
            "attempted_image_count": 0,
            "succeeded_image_count": 0,
            "failed_image_count": 0,
            "cache_write_count": 0,
            "result": "blocked" if not ok else "planned",
            "fixed_error_codes": errors,
            "image_hash_hashes": hash_image_hashes(plan.expected_hashes),
        }
        write_audit_manifest(audit, run_dir)
    except Exception:
        return 1, [ERROR_AUDIT_WRITE], plan
    if not ok:
        return 2, errors, plan
    return 0, [], plan


# ================================================================
# Phase 6.4C2-B1 — Offline fake execution engine
# ================================================================
EXECUTION_PLAN_STATUS_PLANNED = "planned"


@dataclass
class ExternalAnalyzerExecutionSummary:
    """execution 結果摘要（不含任何敏感值）。

    Phase 6.4C2-B1.1 accounting：
    - processed = hits + misses + invalid
    - attempted = 實際 adapter.analyze_image invocation 數（<= misses）
    - loader 失敗 → miss+1、attempted 不增加、failed+1
    """

    run_id: str
    planned_image_count: int
    processed_image_count: int
    attempted_image_count: int
    succeeded_image_count: int
    failed_image_count: int
    cache_hit_count: int
    cache_miss_count: int
    cache_invalid_count: int
    cache_write_count: int
    fixed_error_codes: list[str] = field(default_factory=list)
    status: str = "blocked"

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "planned_image_count": self.planned_image_count,
            "processed_image_count": self.processed_image_count,
            "attempted_image_count": self.attempted_image_count,
            "succeeded_image_count": self.succeeded_image_count,
            "failed_image_count": self.failed_image_count,
            "cache_hit_count": self.cache_hit_count,
            "cache_miss_count": self.cache_miss_count,
            "cache_invalid_count": self.cache_invalid_count,
            "cache_write_count": self.cache_write_count,
            "fixed_error_codes": list(self.fixed_error_codes),
            "status": self.status,
        }


def _is_valid_utc_timestamp(value: object) -> bool:
    """Phase 6.4C2-B1.3：真正 datetime 驗證（非 regex only）。

    - 完整符合 YYYY-MM-DDTHH:MM:SSZ
    - 必須是真實存在的日期與時間（拒 2026-99-99、25:61 等）
    - 不接受 timezone offset / fractional seconds / 非字串
    """
    import datetime
    if not isinstance(value, str):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def _is_valid_run_id(value: object) -> bool:
    """Phase 6.4C2-B1.4：共用 run-ID 驗證（_safe_trace_run_id 與
    preflight 使用同一契約；analyzer_audit 保持相同 regex）。"""
    return (isinstance(value, str)
            and re.fullmatch(r"run-[0-9a-f]{12}", value) is not None)


def _safe_trace_run_id(plan_run_id: object) -> tuple[str, bool]:
    """Phase 6.4C2-B1.3/B1.4：trace identity 契約。

    - plan.run_id 合法 → (plan.run_id, True)（精確保留）
    - 非法（含 None/int/object）→ 安全 run-<12 hex>、不回顯原始值
      → (safe, False)
    """
    if _is_valid_run_id(plan_run_id):
        return plan_run_id, True
    return f"run-{uuid.uuid4().hex[:12]}", False


def _append_error_once(errors: list[str], code: str) -> None:
    """Phase 6.4C2-B1.4：order-preserving unique 固定錯誤碼。"""
    if code not in errors:
        errors.append(code)


def _dedupe_errors(errors: list[str]) -> list[str]:
    """order-preserving 去重（不得用 set 導致順序不穩定）。"""
    return list(dict.fromkeys(errors))


def _execution_preflight(
    *,
    plan: ExternalAnalyzerExecutionPlan,
    eligible_cases: list[EligibleAnalyzerCase],
    loader: Any,
    adapter: Any,
    cache_dir: str | os.PathLike[str],
    audit_dir: str | os.PathLike[str],
    allowed_root: str | os.PathLike[str],
    analyzer_name: str,
    analyzer_version: str,
) -> list[str]:
    """execution preflight：任一失敗 → 不呼叫 loader/adapter、不寫 cache。"""
    errors: list[str] = []
    if plan.status != EXECUTION_PLAN_STATUS_PLANNED:
        errors.append("execution_plan_invalid")
    if plan.dry_run:
        errors.append("execution_plan_dry_run_only")
    if not plan.authorized:
        errors.append("execution_plan_not_authorized")
    if plan.schema_version != PLAN_SCHEMA_VERSION:
        errors.append("execution_plan_invalid")
    # Phase 6.4C2-B1.2/B1.3/B1.4：plan 身份契約
    # （run_id 非字串 → 不做 slicing、不轉路徑、安全 blocked）
    run_id_valid = _is_valid_run_id(plan.run_id)
    if not run_id_valid:
        errors.append("execution_plan_invalid")
    if not _is_valid_utc_timestamp(plan.created_at):
        errors.append("execution_plan_invalid")
    # Phase 6.4C2-B1.4：namespace 驗證（run_id 無效時不 slicing）
    ns = plan.cache_namespace
    expected_namespace = (
        f"namespace-{plan.run_id[4:]}" if run_id_valid else None)
    if not isinstance(ns, str):
        errors.append("execution_plan_invalid")
    elif not re.fullmatch(r"namespace-[0-9a-f]{12}", ns) \
            or expected_namespace is None or ns != expected_namespace:
        errors.append("execution_plan_invalid")
    if loader is None:
        errors.append(ERROR_LOADER_UNAVAILABLE)
    if adapter is None:
        errors.append(ERROR_ADAPTER_UNAVAILABLE)
    try:
        resolve_local_data_subdir(cache_dir, allowed_root)
        resolve_local_data_subdir(audit_dir, allowed_root)
    except ValueError:
        errors.append(ERROR_OUTPUT_PATH)
    # counts 與 eligible_cases 一致
    if plan.case_count != len(eligible_cases):
        errors.append("execution_plan_count_mismatch")
    # case_keys 是 per-image（build_execution_plan 每張圖 append 一次）
    if len(plan.case_keys) != plan.image_count:
        errors.append("execution_plan_count_mismatch")
    flat_hashes = [h for c in eligible_cases for h in c.image_hashes]
    flat_refs = [r for c in eligible_cases for r in c.storage_references]
    if plan.image_count != len(flat_hashes) or plan.image_count != len(flat_refs):
        errors.append("execution_plan_count_mismatch")
    if list(plan.expected_hashes) != flat_hashes:
        errors.append("execution_plan_hash_mismatch")
    # Phase 6.4C2-B1.1：image indexes 為 per-case range（扁平可重複）
    expected_image_indexes = [
        image_index
        for case in eligible_cases
        for image_index in range(len(case.image_hashes))
    ]
    if list(plan.image_indexes) != expected_image_indexes:
        errors.append("execution_plan_count_mismatch")
    for k in plan.case_keys:
        if not (len(k) == 64 and all(c in "0123456789abcdef" for c in k)):
            errors.append("execution_plan_invalid")
    # duplicate (opaque case key, image index) pair（不同 case key 的相同
    # image_index 不算 duplicate）
    seen: set[tuple[str, int]] = set()
    for i in range(plan.image_count):
        item = (plan.case_keys[i], plan.image_indexes[i])
        if item in seen:
            errors.append("duplicate_execution_item")
        seen.add(item)
    # Phase 6.4C2-B1.1：adapter identity binding
    # （caller 不得任意宣稱 analyzer identity；Failing adapter 無
    #  analyzer_name/version 屬性 → 亦視為 mismatch）
    adapter_name = getattr(adapter, "analyzer_name", None)
    adapter_version = getattr(adapter, "analyzer_version", None)
    if adapter_name != analyzer_name or adapter_version != analyzer_version \
            or plan.adapter_name != analyzer_name:
        errors.append("execution_adapter_identity_mismatch")
    return errors


def execute_external_analyzer_plan(
    *,
    plan: ExternalAnalyzerExecutionPlan,
    eligible_cases: list[EligibleAnalyzerCase],
    loader: Any,
    adapter: Any,
    cache_dir: str | os.PathLike[str],
    audit_dir: str | os.PathLike[str],
    allowed_root: str | os.PathLike[str],
    analyzer_name: str,
    analyzer_version: str,
) -> ExternalAnalyzerExecutionSummary:
    """離線 fake execution engine（Phase 6.4C2-B1）。

    validated plan → memory fake bytes → SHA-256 驗證 → fake adapter →
    normalized result 驗證 → atomic cache write → atomic audit write →
    per-image failure containment → deterministic rerun / cache reuse。
    """
    from alkaid_cs2.evaluation.analyzer_audit import (
        AUDIT_SCHEMA_VERSION_V2,
        hash_image_hashes,
        validate_audit_manifest,
        write_audit_manifest,
    )
    from alkaid_cs2.evaluation.analyzer_cache import (
        build_cache_record,
        compute_analyzer_cache_key,
        load_analyzer_cache_record,
        validate_normalized_result,
        write_analyzer_cache_record,
    )
    from alkaid_cs2.evaluation.secure_image_loader import (
        SecureImageLoadError,
    )

    # Phase 6.4C2-B1.3：started_at 必須在 preflight 之前產生
    #（audit 時間涵蓋 preflight）
    started = _now_utc()

    preflight_errors = _execution_preflight(
        plan=plan, eligible_cases=eligible_cases, loader=loader,
        adapter=adapter, cache_dir=cache_dir, audit_dir=audit_dir,
        allowed_root=allowed_root, analyzer_name=analyzer_name,
        analyzer_version=analyzer_version)

    # Phase 6.4C2-B1.2/B1.3/B1.4：run_id 統一由 plan 提供；非法 run_id →
    # 安全 blocked trace ID（不回顯原始值、不當路徑片段、不誤報
    # audit_write_failed、錯誤碼去重）
    run_id, run_id_valid = _safe_trace_run_id(plan.run_id)
    if not run_id_valid:
        _append_error_once(preflight_errors, "execution_plan_invalid")
    preflight_errors = _dedupe_errors(preflight_errors)
    # run_id 非法 → preflight_errors 已含 execution_plan_invalid → 下方 blocked

    def _summary(status, codes, **kw) -> ExternalAnalyzerExecutionSummary:
        return ExternalAnalyzerExecutionSummary(
            run_id=run_id, planned_image_count=plan.image_count,
            processed_image_count=kw.get("processed", 0),
            attempted_image_count=kw.get("attempted", 0),
            succeeded_image_count=kw.get("succeeded", 0),
            failed_image_count=kw.get("failed", 0),
            cache_hit_count=kw.get("hits", 0),
            cache_miss_count=kw.get("misses", 0),
            cache_invalid_count=kw.get("invalid", 0),
            cache_write_count=kw.get("writes", 0),
            fixed_error_codes=list(codes), status=status)

    def _write_audit(extra: dict) -> None:
        audit = {
            "schema_version": AUDIT_SCHEMA_VERSION_V2,
            "run_id": run_id,
            "started_at": started,
            "completed_at": _now_utc(),  # B1.2：寫入時才產生
            "dry_run": False,
            "authorization_flag_present": plan.authorized,
            "authorization_env_present": True,
            "eligible_case_count": plan.case_count,
            "eligible_image_count": plan.image_count,
            "processed_image_count": extra["processed"],
            "attempted_image_count": extra["attempted"],
            "succeeded_image_count": extra["succeeded"],
            "failed_image_count": extra["failed"],
            "cache_hit_count": extra["hits"],
            "cache_miss_count": extra["misses"],
            "cache_invalid_count": extra["invalid"],
            "cache_write_count": extra["writes"],
            "result": extra["result"],
            "fixed_error_codes": list(extra["codes"]),
            "image_hash_hashes": hash_image_hashes(plan.expected_hashes),
            "analyzer_name": analyzer_name,
            "analyzer_version": analyzer_version,
        }
        errors = validate_audit_manifest(audit)
        if errors:
            raise ValueError("audit_validation_failed")
        run_dir = Path(audit_dir) / run_id
        write_audit_manifest(audit, run_dir)

    if preflight_errors:
        try:
            _write_audit({
                "processed": 0, "attempted": 0, "succeeded": 0, "failed": 0,
                "hits": 0, "misses": 0, "invalid": 0, "writes": 0,
                "result": "blocked", "codes": preflight_errors})
        except Exception:
            # Phase 6.4C2-B1.1：blocked audit 寫入失敗不得靜默吞掉
            if "audit_write_failed" not in preflight_errors:
                preflight_errors.append("audit_write_failed")
        return _summary("blocked", preflight_errors)

    processed = attempted = succeeded = failed = 0
    hits = misses = invalid = writes = 0
    error_codes: list[str] = []
    idx = 0
    for case in eligible_cases:
        for image_idx, (ref, sha) in enumerate(
                zip(case.storage_references, case.image_hashes)):
            # case_keys 是 per-image（順序與 flat hashes 一致）
            opaque_key = plan.case_keys[idx]
            idx += 1
            processed += 1
            cache_key = compute_analyzer_cache_key(
                opaque_case_key=opaque_key, image_index=image_idx,
                image_sha256=sha, analyzer_name=analyzer_name,
                analyzer_version=analyzer_version)
            record, cache_errs = load_analyzer_cache_record(
                cache_key, cache_dir, analyzer_name=analyzer_name,
                analyzer_version=analyzer_version)
            if record is not None:
                # cache hit：不呼叫 loader/adapter、不重寫 cache
                hits += 1
                succeeded += 1
                continue
            if cache_errs:
                # corrupted cache（Phase 6.4C2-B1.1）：invalid+1、failed+1、
                # 不呼叫 loader、不呼叫 adapter、不重寫 cache
                invalid += 1
                failed += 1
                for e in cache_errs:
                    if e not in error_codes:
                        error_codes.append(e)
                continue
            # cache miss：進入 loader path（attempted 在真正呼叫 adapter 前不增加）
            misses += 1
            try:
                image_bytes = loader.load(ref, sha)
            except SecureImageLoadError as exc:
                failed += 1
                # Phase 6.4C2-B1.1：保留固定碼，不把未知錯誤映射成 not_found
                code = str(exc)
                mapped = code if code in (
                    "secure_reference_invalid", "secure_image_not_found",
                    "secure_image_hash_mismatch") else \
                    "secure_image_loader_failed"
                if mapped not in error_codes:
                    error_codes.append(mapped)
                continue
            except Exception:
                failed += 1
                if "secure_image_loader_failed" not in error_codes:
                    error_codes.append("secure_image_loader_failed")
                continue
            # 真正呼叫 adapter 前才計 attempted
            attempted += 1
            try:
                result = adapter.analyze_image(
                    image_bytes, case_key=opaque_key, image_index=image_idx)
            except Exception:
                failed += 1
                if ERROR_ADAPTER_FAILED not in error_codes:
                    error_codes.append(ERROR_ADAPTER_FAILED)
                continue
            result_errors = validate_normalized_result(result)
            if result_errors:
                failed += 1
                if ERROR_RESULT_INVALID not in error_codes:
                    error_codes.append(ERROR_RESULT_INVALID)
                continue
            try:
                record_data = build_cache_record(
                    opaque_case_key=opaque_key, image_index=image_idx,
                    image_sha256=sha, analyzer_name=analyzer_name,
                    analyzer_version=analyzer_version,
                    analyzed_at=_now_utc(), normalized_result=result)
                write_analyzer_cache_record(
                    record_data, cache_dir, cache_key=cache_key)
            except Exception:
                failed += 1
                if ERROR_CACHE_WRITE not in error_codes:
                    error_codes.append(ERROR_CACHE_WRITE)
                continue
            succeeded += 1
            writes += 1

    if plan.image_count > 0 and failed == plan.image_count:
        status = "failed"
    elif failed == 0:
        status = "completed"
    else:
        status = "completed_with_failures"
    try:
        _write_audit({
            "processed": processed, "attempted": attempted,
            "succeeded": succeeded, "failed": failed,
            "hits": hits, "misses": misses, "invalid": invalid,
            "writes": writes, "result": status, "codes": error_codes})
    except Exception:
        # Phase 6.4C2-B1.1：audit 寫入失敗不得靜默吞掉；
        # 不影響已完成的 cache；summary 含 audit_write_failed
        if ERROR_AUDIT_WRITE not in error_codes:
            error_codes.append(ERROR_AUDIT_WRITE)
        if status == "completed":
            status = "completed_with_failures"
    return _summary(status, error_codes, processed=processed,
                    attempted=attempted, succeeded=succeeded, failed=failed,
                    hits=hits, misses=misses, invalid=invalid, writes=writes)
