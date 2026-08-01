# -*- coding: utf-8 -*-
"""
analyzer_audit.py — Execution audit manifest（Phase 6.4C2-B0 / B0.1）

- 每次 execution / dry-run 寫入 Git 外：local_data/evaluation_analyzer_runs/<run_id>/audit.json
- 嚴格 allowlist schema（validate_audit_manifest）
- 原子寫入：canonical bytes → 唯一 temp → write+flush+fsync → os.replace
- 可記錄 image hash 的再次雜湊（不存原 hash）
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from alkaid_cs2.evaluation.intake_validation import (
    scan_redaction_issues,
)

AUDIT_SCHEMA_VERSION = "analyzer-audit-v1"
AUDIT_SCHEMA_VERSION_V2 = "external-analyzer-audit-v2"

ALLOWED_AUDIT_FIELDS = frozenset({
    "schema_version", "run_id", "started_at", "completed_at", "dry_run",
    "authorization_flag_present", "authorization_env_present",
    "eligible_case_count", "eligible_image_count", "attempted_image_count",
    "succeeded_image_count", "failed_image_count", "cache_write_count",
    "result", "fixed_error_codes", "image_hash_hashes",
})

# Phase 6.4C2-B1：v2 只新增欄位（不改變 v1 語意）；B1.1 加 cache_invalid_count
ALLOWED_AUDIT_FIELDS_V2 = ALLOWED_AUDIT_FIELDS | frozenset({
    "processed_image_count", "cache_hit_count", "cache_miss_count",
    "cache_invalid_count", "analyzer_name", "analyzer_version",
})

ALLOWED_AUDIT_RESULTS = frozenset(
    {"blocked", "planned", "completed", "failed", "completed_with_failures"})

# 已知固定錯誤碼（Phase 6.4C2-B0 / B0.1 / B1）
KNOWN_ERROR_CODES = frozenset({
    "external_analyzer_flag_missing", "external_analyzer_env_missing",
    "external_analyzer_not_authorized", "no_eligible_real_cases",
    "no_eligible_real_images", "secure_image_loader_unavailable",
    "analyzer_adapter_unavailable", "secure_reference_invalid",
    "secure_image_not_found", "secure_image_hash_mismatch",
    "analyzer_execution_failed", "analyzer_result_invalid",
    "cache_write_failed", "audit_write_failed",
    "manifest_missing", "manifest_invalid_json",
    "manifest_validation_failed", "output_path_not_allowed",
    "cache_record_invalid", "audit_validation_failed",
    "manifest_case_fixture_missing", "manifest_case_fixture_invalid",
    "manifest_fixture_hash_mismatch", "manifest_fixture_case_id_mismatch",
    "manifest_fixture_source_mismatch", "manifest_image_hash_invalid",
    "manifest_image_count_mismatch", "manifest_invalid_storage_reference",
    "manifest_fixture_privacy_failed",
    # Phase 6.4C2-B1：execution engine
    "execution_plan_invalid", "execution_plan_not_authorized",
    "execution_plan_dry_run_only", "execution_plan_count_mismatch",
    "execution_plan_hash_mismatch", "duplicate_execution_item",
    "cache_identity_mismatch", "cache_read_failed",
    # Phase 6.4C2-B1.1：loader error mapping / adapter identity binding
    "secure_image_loader_failed", "execution_adapter_identity_mismatch",
})

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_RUN_ID_RE = re.compile(r"^run-[0-9a-f]{12}$")
_UTC_TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AnalyzerAuditWriteError(Exception):
    """audit manifest 寫入失敗（固定錯誤碼）。"""


def _is_utc_timestamp(value: str) -> bool:
    import datetime
    if not _UTC_TS_RE.match(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def hash_image_hashes(image_hashes: list[str]) -> list[str]:
    """image hash 的再次雜湊（不存原 hash 值）。"""
    return [hashlib.sha256(h.encode("utf-8")).hexdigest()
            for h in image_hashes]


def validate_audit_manifest(audit: dict) -> list[str]:
    """嚴格 audit schema 驗證（Phase 6.4C2-B0.1 / B1 v2）；回傳錯誤清單。

    - v1（analyzer-audit-v1）：B0 dry-run 用
    - v2（external-analyzer-audit-v2）：B1 execution 用（新增 counts/analyzer）
    """
    errors: list[str] = []
    version = audit.get("schema_version")
    if version == AUDIT_SCHEMA_VERSION:
        allowed = ALLOWED_AUDIT_FIELDS
    elif version == AUDIT_SCHEMA_VERSION_V2:
        allowed = ALLOWED_AUDIT_FIELDS_V2
    else:
        return ["schema_version_invalid"]
    unknown = set(audit) - allowed
    if unknown:
        errors.append(f"unknown_fields:{','.join(sorted(unknown))}")
    if not _RUN_ID_RE.match(str(audit.get("run_id", ""))):
        errors.append("run_id_invalid")
    for ts_field in ("started_at", "completed_at"):
        v = audit.get(ts_field)
        if not isinstance(v, str) or not _is_utc_timestamp(v):
            errors.append(f"{ts_field}_invalid")
    for bool_field in ("dry_run", "authorization_flag_present",
                       "authorization_env_present"):
        if not isinstance(audit.get(bool_field), bool):
            errors.append(f"{bool_field}_invalid")
    count_fields = ["eligible_case_count", "eligible_image_count",
                    "attempted_image_count", "succeeded_image_count",
                    "failed_image_count", "cache_write_count"]
    if version == AUDIT_SCHEMA_VERSION_V2:
        count_fields += ["processed_image_count", "cache_hit_count",
                         "cache_miss_count", "cache_invalid_count"]
    for count_field in count_fields:
        v = audit.get(count_field)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            errors.append(f"{count_field}_invalid")
    if version == AUDIT_SCHEMA_VERSION_V2:
        for name_field in ("analyzer_name", "analyzer_version"):
            if not isinstance(audit.get(name_field), str) \
                    or not audit.get(name_field):
                errors.append(f"{name_field}_invalid")
    succeeded = audit.get("succeeded_image_count", 0)
    failed = audit.get("failed_image_count", 0)
    attempted = audit.get("attempted_image_count", 0)
    if isinstance(succeeded, int) and isinstance(failed, int) and \
            isinstance(attempted, int):
        if version == AUDIT_SCHEMA_VERSION:
            # v1：attempted = 處理 image 數（含 hit/miss）
            if succeeded + failed > attempted:
                errors.append("count_sum_exceeds_attempted")
    if version == AUDIT_SCHEMA_VERSION_V2:
        processed = audit.get("processed_image_count", 0)
        hits = audit.get("cache_hit_count", 0)
        misses = audit.get("cache_miss_count", 0)
        invalid = audit.get("cache_invalid_count", 0)
        if isinstance(processed, int) and isinstance(hits, int) and \
                isinstance(misses, int) and isinstance(invalid, int) and \
                isinstance(attempted, int):
            # Phase 6.4C2-B1.1 語意：
            # processed == hits + misses + invalid
            # attempted（adapter invocation）<= misses
            if processed != hits + misses + invalid:
                errors.append("processed_neq_hits_plus_misses_plus_invalid")
            if attempted > misses:
                errors.append("attempted_exceeds_misses")
            if succeeded + failed != processed:
                errors.append("succeeded_failed_neq_processed")
    cache_writes = audit.get("cache_write_count", 0)
    if isinstance(succeeded, int) and isinstance(cache_writes, int) and \
            cache_writes > succeeded:
        errors.append("cache_write_exceeds_succeeded")
    if audit.get("result") not in ALLOWED_AUDIT_RESULTS:
        errors.append("result_invalid")
    codes = audit.get("fixed_error_codes", [])
    if not isinstance(codes, list) or \
            not all(isinstance(c, str) for c in codes):
        errors.append("fixed_error_codes_invalid")
    else:
        for c in codes:
            if c not in KNOWN_ERROR_CODES:
                errors.append(f"unknown_error_code:{c}")
    hashes = audit.get("image_hash_hashes", [])
    if not isinstance(hashes, list) or \
            not all(isinstance(h, str) and _SHA256_RE.match(h)
                    for h in hashes):
        errors.append("image_hash_hashes_invalid")
    findings = scan_redaction_issues(audit)
    for f in findings:
        if f.severity == "error":
            errors.append(f"privacy:{f.code}:{f.field}")
    return errors


def _canonical_bytes(audit: dict) -> bytes:
    return json.dumps(audit, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def write_audit_manifest(
    audit: dict,
    run_dir: str | os.PathLike[str],
) -> str:
    """原子寫入 audit manifest；回傳路徑。

    - 完整驗證（validate_audit_manifest）
    - 失敗 → AnalyzerAuditWriteError（固定碼，不含路徑/值）
    """
    audit_errors = validate_audit_manifest(audit)
    if audit_errors:
        raise AnalyzerAuditWriteError("audit_validation_failed")
    data = _canonical_bytes(audit)
    try:
        d = Path(run_dir)
        d.mkdir(parents=True, exist_ok=True)
        target = d / "audit.json"
        tmp = d / f".audit.json.tmp.{uuid.uuid4().hex[:8]}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
    except OSError:
        raise AnalyzerAuditWriteError("audit_write_failed") from None
    return str(target)
