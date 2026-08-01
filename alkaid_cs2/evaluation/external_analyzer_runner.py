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
        cache_namespace=f"namespace-{run_id}",
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
