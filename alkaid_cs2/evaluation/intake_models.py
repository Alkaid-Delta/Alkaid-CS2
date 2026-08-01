# -*- coding: utf-8 -*-
"""
intake_models.py — 真實案例 Intake Manifest 資料模型（Phase 6.4C2-A）

RealCaseIntakeManifest 記錄真實案例從收集→匿名化→雙人審核→裁決的
完整 provenance 鏈。原始資料一律保存在 Git 外（secure-store://<opaque-id>），
repository 內只保存 manifest 與匿名化後的最小欄位。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum


class ProvenanceSource(str, Enum):
    """真實案例的合法來源（allowlist）。

    不得包含 agent_generated / synthetic / inferred_real。
    """
    USER_SUPPLIED_REAL = "user_supplied_real"
    USER_AUTHORIZED_COLLECTION = "user_authorized_collection"
    INTERNAL_OWNED_SOURCE = "internal_owned_source"


PROVENANCE_ALLOWLIST = frozenset(p.value for p in ProvenanceSource)
PROHIBITED_PROVENANCE = frozenset(
    {"agent_generated", "synthetic", "inferred_real", "manual_fixture",
     "adversarial_synthetic"})


class AuthorizationStatus(str, Enum):
    """授權狀態 allowlist（未知/空白不得進 anonymized_real）。"""
    USER_SUPPLIED = "user_supplied"
    OWNER_AUTHORIZED = "owner_authorized"
    INTERNAL_OWNED = "internal_owned"


AUTHORIZATION_ALLOWLIST = frozenset(a.value for a in AuthorizationStatus)


class RedactionStatus(str, Enum):
    NOT_REDACTED = "not_redacted"
    PARTIAL = "partial"
    COMPLETE = "complete"


class PrivacyScanStatus(str, Enum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"


class ReviewWorkflowStatus(str, Enum):
    """review workflow 狀態（個別 reviewer 與最終狀態）。"""
    PENDING = "pending"
    COMPLETED = "completed"


class AdjudicationStatus(str, Enum):
    NOT_NEEDED = "not_needed"
    NEEDED = "needed"
    COMPLETED = "completed"


class FinalReviewStatus(str, Enum):
    """最終 review 狀態（與 GroundTruthReviewStatus 對齊）。"""
    SINGLE_REVIEW = "single_review"
    DOUBLE_REVIEW = "double_review"
    DISPUTED = "disputed"


REVIEWER_ID_ALLOWLIST = frozenset({"reviewer_a", "reviewer_b", "reviewer_c"})

# secure-store://<opaque-id>：opaque id 限小寫字母/數字/連字號/底線
_SECURE_STORE_RE = re.compile(r"^secure-store://[a-z0-9][a-z0-9_-]*$")
_ABSOLUTE_PATH_RE = re.compile(r"^(?:[A-Za-z]:[\\/]|[/\\])")
_HTTP_RE = re.compile(r"^https?://", re.IGNORECASE)


def validate_secure_store_reference(ref: str) -> bool:
    """secure image/source reference 必須是 secure-store://<opaque-id>。

    不得 http/https、不得本機絕對路徑、不得含 token/帳號。
    """
    if not isinstance(ref, str) or not ref:
        return False
    if _HTTP_RE.match(ref):
        return False
    if _ABSOLUTE_PATH_RE.match(ref):
        return False
    return bool(_SECURE_STORE_RE.match(ref))


def validate_provenance(value: str) -> bool:
    return isinstance(value, str) and value in PROVENANCE_ALLOWLIST


def validate_authorization(value: str) -> bool:
    return isinstance(value, str) and value in AUTHORIZATION_ALLOWLIST


def validate_reviewer_id(value: str) -> bool:
    return isinstance(value, str) and value in REVIEWER_ID_ALLOWLIST


@dataclass
class RealCaseIntakeManifest:
    """真實案例 intake manifest（schema v1）。

    規則：
    - source_provenance 必須是 ProvenanceSource allowlist
    - consent_or_authorization 必須是 AuthorizationStatus allowlist
    - original_storage_reference 必須是 secure-store://<opaque-id>
    - 不保存 raw_text 全文、payload、image bytes
    """
    intake_id: str
    case_id: str
    source_type: str
    source_provenance: str
    consent_or_authorization: str
    original_storage_reference: str
    redaction_version: str
    collected_at: str | None = None
    collection_method: str | None = None
    redaction_status: str = RedactionStatus.NOT_REDACTED.value
    redacted_by: str | None = None
    privacy_scan_status: str = PrivacyScanStatus.PENDING.value
    reviewer_a_status: str = ReviewWorkflowStatus.PENDING.value
    reviewer_b_status: str = ReviewWorkflowStatus.PENDING.value
    adjudication_status: str = AdjudicationStatus.NOT_NEEDED.value
    final_review_status: str | None = None
    image_count: int = 0
    original_image_hashes: list[str] = field(default_factory=list)
    redacted_image_hashes: list[str] = field(default_factory=list)
    notes: str | None = None
    schema_version: str = "intake-manifest-v1"

    def __post_init__(self) -> None:
        for f in ("intake_id", "case_id", "source_type"):
            v = getattr(self, f)
            if not isinstance(v, str) or not v.strip():
                raise ValueError(f"{f} 必須非空 str")
        if not validate_provenance(self.source_provenance):
            raise ValueError(
                f"source_provenance 不合格（allowlist 不含 {self.source_provenance!r}；"
                "不允許 agent_generated/synthetic/inferred_real）")
        if not validate_authorization(self.consent_or_authorization):
            raise ValueError(
                f"consent_or_authorization 不合格（allowlist 不含 "
                f"{self.consent_or_authorization!r}）")
        if not validate_secure_store_reference(self.original_storage_reference):
            raise ValueError(
                "original_storage_reference 必須是 secure-store://<opaque-id>"
                "（不得 http/https、絕對路徑、帳號/token）")
        if not isinstance(self.redaction_version, str) or not self.redaction_version.strip():
            raise ValueError("redaction_version 必填")
        if self.redacted_by is not None and \
                not validate_reviewer_id(self.redacted_by) and \
                self.redacted_by != "user":
            raise ValueError(f"redacted_by 不合格：{self.redacted_by!r}")
        if isinstance(self.image_count, bool) or not isinstance(self.image_count, int):
            raise TypeError("image_count 必須 int")
        if self.image_count < 0:
            raise ValueError("image_count 不可為負數")
        # Phase 6.4C2-A.3：hash 驗證下沉至 model（直接建構也不得繞過）
        from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
            validate_image_hashes,
        )
        hash_errors = validate_image_hashes(
            original_image_hashes=self.original_image_hashes,
            redacted_image_hashes=self.redacted_image_hashes,
            image_count=self.image_count)
        if hash_errors:
            raise ValueError(f"image hash 驗證失敗：{', '.join(hash_errors)}")

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items()}
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "RealCaseIntakeManifest":
        allowed = {f for f in cls.__dataclass_fields__}
        unknown = set(data) - allowed
        if unknown:
            raise ValueError(f"未知欄位：{sorted(unknown)}")
        return cls(**{k: data[k] for k in allowed if k in data})


def can_mark_anonymized_real(
    *,
    provenance: str,
    authorization: str,
    redaction_version: str | None,
    privacy_error_count: int,
    has_image_bytes: bool,
    has_http: bool,
    has_private_fields: bool,
) -> tuple[bool, list[str]]:
    """只有全部成立才能標 anonymized_real：

    - provenance 合格（allowlist）
    - authorization 合格（allowlist）
    - privacy scanner 0 error
    - 無 image bytes
    - 無 http/https
    - 無私人欄位
    - redaction_version 存在

    否則 → draft（unverified_real_draft）。
    """
    reasons: list[str] = []
    if not validate_provenance(provenance):
        reasons.append("provenance_invalid")
    if not validate_authorization(authorization):
        reasons.append("authorization_invalid")
    if not redaction_version:
        reasons.append("redaction_version_missing")
    if privacy_error_count > 0:
        reasons.append("privacy_errors_present")
    if has_image_bytes:
        reasons.append("image_bytes_present")
    if has_http:
        reasons.append("http_url_present")
    if has_private_fields:
        reasons.append("private_fields_present")
    return (len(reasons) == 0), reasons


# ================================================================
# evaluation_real manifest.json 驗證（Phase 6.4C2-A §11）
# ================================================================
MANIFEST_SCHEMA_VERSION = "evaluation-real-manifest-v1"
MANIFEST_REQUIRED_FIELDS = (
    "schema_version",
    "cases",
    "real_case_count",
    "double_reviewed_real_count",
    "disputed_real_count",
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_real_manifest(manifest: dict, real_cases: list) -> tuple[bool, list[str]]:
    """驗證 evaluation_real/manifest.json。

    - schema 欄位齊全且版本正確
    - cases 每筆必填（6.4C2-A.2）：
      case_id / source=="anonymized_real" / source_provenance /
      authorization_status / redaction_version / privacy_scan_status /
      review_status / fixture_sha256 / image_reference_count / analyzer_cache_status
    - 未知欄位拒絕；整個 entry 遞迴 privacy scan（不得含 raw_text/URL/bytes...）
    - real_case_count 必須 == cases 中 anonymized_real 數量（推導一致）
    - double/disputed count 必須與 real_cases 的 review status 推導一致

    real_cases：已載入的 EvaluationCase 清單（source=anonymized_real）。
    """
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource, GroundTruthReviewStatus,
    )
    from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
        scan_redaction_issues,
    )
    reasons: list[str] = []
    if not isinstance(manifest, dict):
        return False, ["manifest_not_object"]
    for f in MANIFEST_REQUIRED_FIELDS:
        if f not in manifest:
            reasons.append(f"missing_field:{f}")
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        reasons.append(f"schema_version_mismatch:{manifest.get('schema_version')!r}")
    cases_list = manifest.get("cases")
    if not isinstance(cases_list, list):
        reasons.append("cases_not_list")
    else:
        entry_required = (
            "case_id", "source", "source_provenance", "authorization_status",
            "redaction_version", "privacy_scan_status", "review_status",
            "fixture_sha256", "image_reference_count", "analyzer_cache_status")
        for i, entry in enumerate(cases_list):
            if not isinstance(entry, dict):
                reasons.append(f"cases[{i}]_not_object")
                continue
            for f in entry_required:
                if f not in entry or entry[f] in ("", None):
                    reasons.append(f"cases[{i}].{f}_missing")
            if entry.get("source") != "anonymized_real":
                reasons.append(f"cases[{i}].source_not_anonymized_real")
            if not validate_provenance(str(entry.get("source_provenance") or "")):
                reasons.append(f"cases[{i}].provenance_invalid")
            if not validate_authorization(
                    str(entry.get("authorization_status") or "")):
                reasons.append(f"cases[{i}].authorization_status_invalid")
            if entry.get("privacy_scan_status") != "passed":
                reasons.append(f"cases[{i}].privacy_scan_status_not_passed")
            if not _SHA256_RE.match(str(entry.get("fixture_sha256") or "")):
                reasons.append(f"cases[{i}].fixture_sha256_invalid")
            if entry.get("review_status") not in (
                    "single_review", "disputed", "double_review"):
                reasons.append(f"cases[{i}].review_status_invalid")
            if str(entry.get("analyzer_cache_status") or "") not in (
                    "not_run", "cached", "external"):
                reasons.append(f"cases[{i}].analyzer_cache_status_invalid")
            irc = entry.get("image_reference_count")
            if isinstance(irc, bool) or not isinstance(irc, int) or irc < 0:
                reasons.append(f"cases[{i}].image_reference_count_invalid")
            unknown = set(entry) - set(entry_required)
            if unknown:
                reasons.append(
                    f"cases[{i}].unknown_fields:{','.join(sorted(unknown))}")
            # 遞迴 privacy scan（nested；hash 欄位豁免 base64 heuristic）
            findings = scan_redaction_issues(entry)
            errs = [f for f in findings if f.severity == "error"]
            if errs:
                codes = sorted({f.code for f in errs})
                reasons.append(f"cases[{i}].privacy:{','.join(codes)}")

    # Phase 6.4C2-A.3：counts 全部由 entries 推導（無論 real_cases 是否為空）
    entries = [c for c in cases_list or [] if isinstance(c, dict)]
    real_entries = [c for c in entries
                    if c.get("source") == "anonymized_real"]
    if manifest.get("real_case_count") != len(real_entries):
        reasons.append(
            f"real_case_count_mismatch:manifest={manifest.get('real_case_count')} "
            f"derived={len(real_entries)}")
    entry_double = sum(1 for c in real_entries
                       if c.get("review_status") == "double_review")
    entry_disputed = sum(1 for c in real_entries
                         if c.get("review_status") == "disputed")
    if manifest.get("double_reviewed_real_count") != entry_double:
        reasons.append(
            f"double_reviewed_count_mismatch:manifest="
            f"{manifest.get('double_reviewed_real_count')} derived={entry_double}")
    if manifest.get("disputed_real_count") != entry_disputed:
        reasons.append(
            f"disputed_count_mismatch:manifest="
            f"{manifest.get('disputed_real_count')} derived={entry_disputed}")
    # 與載入的 EvaluationCase 交叉檢查（Phase 6.4C2-A.4：list|None 語意）
    # None=未提供 loaded dataset（不檢查）；[]=已提供但 real=0（必須檢查）
    if real_cases is not None:
        real_anon = [c for c in real_cases
                     if c.source == EvaluationSource.ANONYMIZED_REAL]
        loaded_double = sum(1 for c in real_anon
                            if c.ground_truth_review_status ==
                            GroundTruthReviewStatus.DOUBLE_REVIEW)
        loaded_disputed = sum(1 for c in real_anon
                              if c.ground_truth_review_status ==
                              GroundTruthReviewStatus.DISPUTED)
        if len(real_anon) != manifest.get("real_case_count"):
            reasons.append(
                f"loaded_fixture_count_mismatch:manifest="
                f"{manifest.get('real_case_count')} loaded={len(real_anon)}")
        if entry_double != loaded_double:
            reasons.append(
                f"double_cross_check_mismatch:entries={entry_double} "
                f"loaded={loaded_double}")
        if entry_disputed != loaded_disputed:
            reasons.append(
                f"disputed_cross_check_mismatch:entries={entry_disputed} "
                f"loaded={loaded_disputed}")
    return (len(reasons) == 0), reasons


def intake_ready(manifest: dict | None, real_cases: list) -> tuple[bool, list[str]]:
    """intake ready：manifest 存在且 schema 驗證通過（workflow 可用）。

    - manifest None（缺檔）→ not ready
    - schema 驗證失敗 → not ready
    - 只表示 intake/review workflow 可用，不代表模型準確或 production ready
    """
    if manifest is None:
        return False, ["manifest_missing"]
    return validate_real_manifest(manifest, real_cases)
