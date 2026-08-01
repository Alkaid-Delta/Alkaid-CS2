# -*- coding: utf-8 -*-
"""
intake_validation.py — 匿名化驗證與 review 決策（Phase 6.4C2-A）

全部 deterministic、離線、不呼叫 LLM。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from alkaid_cs2.evaluation.intake_models import (
    AUTHORIZATION_ALLOWLIST,
    PROVENANCE_ALLOWLIST,
    REVIEWER_ID_ALLOWLIST,
    validate_secure_store_reference,
)

# ---------------------------------------------------------------
# 敏感欄位偵測（redaction 驗證）
# ---------------------------------------------------------------
_BINARY_KEYS = frozenset({
    "raw_bytes", "image_bytes", "bytes", "image_base64", "base64",
    "data_url", "thumbnail", "exif", "exif_data",
})
_AUTH_KEYS = frozenset({
    "authorization", "cookie", "cookies", "token", "api_key", "apikey",
    "access_token", "refresh_token", "session", "secret", "password",
})
_PRIVATE_KEYS = frozenset({
    "sender", "recipient", "author_id", "facebook_id", "profile_url",
    "user_id", "account_id", "phone", "email", "real_name", "full_name",
})
_HTTP_RE = re.compile(r"https?://", re.IGNORECASE)
_FACEBOOK_RE = re.compile(r"(?:facebook\.com|fbcdn\.net|fb\.me)", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_TW_MOBILE_RE = re.compile(r"(?:09\d{8}|8869\d{8}|\+8869\d{8})")
_LONG_ID_RE = re.compile(r"\b\d{10,}\b")
# Phase 6.4C2-A.5：自由文字（notes 等）內的 auth 關鍵字
_AUTH_KEYWORD_RE = re.compile(
    r"\b(?:token|cookie|authorization|api[_-]?key|secret|password)\b",
    re.IGNORECASE)
# Phase 6.4C2-B0.1/B0.2：auth_keyword 豁免只限受控 storage reference 欄位
STORAGE_REFERENCE_FIELDS = frozenset({
    "original_storage_reference",
    "storage_reference",
    "secure_storage_reference",
})

# Phase 6.4C2-B0.2：base64 heuristic 豁免只限受控 hash 欄位
SHA256_EXEMPT_FIELDS = frozenset({
    "fixture_sha256",
    "original_image_hashes",
    "redacted_image_hashes",
    "reviewer_inputs_hash",
    "final_ground_truth_hash",
    "image_sha256",
    "result_sha256",
    "image_hash_hashes",
    "expected_hashes",
    "opaque_case_key",
})
_BASE64_RE = re.compile(
    r"data:image/[a-z0-9.+-]+;base64,|"
    r"[A-Za-z0-9+/]{40,}={0,2}",
    re.IGNORECASE)
_LOCAL_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/](?![\\/])|[/\\]{1,2}[A-Za-z0-9_.-]+[\\/])")


class RedactionFinding:
    """匿名化驗證發現（field 可為 nested path，例如 images[0].payload.items[0].token）。"""

    def __init__(self, code: str, field: str, severity: str = "error"):
        self.code = code
        self.field = field
        self.severity = severity

    def to_dict(self) -> dict:
        return {"code": self.code, "field": self.field, "severity": self.severity}

    def __repr__(self) -> str:
        return f"RedactionFinding({self.code}, {self.field})"


def _is_valid_storage_reference_field(field: str, value: str) -> bool:
    """auth_keyword 豁免判定：欄位名在 allowlist 且值通過專用 validator。"""
    field_name = field.rsplit(".", 1)[-1]
    field_name = field_name.split("[", 1)[0].lower()
    return (
        field_name in STORAGE_REFERENCE_FIELDS
        and isinstance(value, str)
        and validate_secure_store_reference(value)
    )


def _is_controlled_sha256_field(field: str, value: Any) -> bool:
    """base64 豁免判定（Phase 6.4C2-B0.2）：

    - nested path 最後欄位名在 SHA256_EXEMPT_FIELDS
    - str 且匹配 ^[0-9a-f]{64}$
    - list 欄位：全部元素合法才豁免
    自由文字（notes/metadata/payload/warning/description/arbitrary_field）
    即使內容是 64 位小寫 hex 也不豁免。
    """
    field_name = field.rsplit(".", 1)[-1]
    field_name = field_name.split("[", 1)[0].lower()
    if field_name not in SHA256_EXEMPT_FIELDS:
        return False
    if isinstance(value, str):
        return bool(_SHA256_RE.match(value))
    if isinstance(value, list):
        return bool(value) and all(
            isinstance(v, str) and _SHA256_RE.match(v) for v in value)
    return False


def _scan_string(value: str, field: str, findings: list[RedactionFinding]) -> None:
    if not value:
        return
    if _HTTP_RE.search(value):
        findings.append(RedactionFinding("http_url", field))
    if _FACEBOOK_RE.search(value):
        findings.append(RedactionFinding("facebook_url", field))
    if _EMAIL_RE.search(value):
        findings.append(RedactionFinding("email", field))
    if _TW_MOBILE_RE.search(value):
        findings.append(RedactionFinding("tw_mobile", field))
    if _LONG_ID_RE.search(value):
        findings.append(RedactionFinding("long_numeric_id", field))
    # Phase 6.4C2-A.8.1：豁免只限「受控 storage reference 欄位 + 值合法」；
    # notes/metadata/任意欄位即使含 secure-store:// 前綴也不豁免；
    # 只豁免 auth_keyword，其他規則（HTTP/path/email/phone/base64）照常執行
    is_valid_storage_ref = _is_valid_storage_reference_field(field, value)
    if _AUTH_KEYWORD_RE.search(value) and not is_valid_storage_ref:
        findings.append(RedactionFinding("auth_keyword", field))
    # Phase 6.4C2-B0.2：base64 豁免只限受控 hash 欄位（非全域 64-hex 跳過）
    if _BASE64_RE.search(value) and "base64" not in field.lower() \
            and not _is_controlled_sha256_field(field, value):
        findings.append(RedactionFinding("base64_like", field))
    if _LOCAL_PATH_RE.search(value):
        findings.append(RedactionFinding("local_path", field))


# Phase 6.4C2-B0.2：統一由 _is_controlled_sha256_field 處理（只豁免 base64；
# email/phone/http/path/auth 等規則照常執行）


def _scan_value(value: Any, field: str, findings: list[RedactionFinding],
                depth: int = 0) -> None:
    if depth > 20:
        findings.append(RedactionFinding("depth_limit", field))
        return
    if isinstance(value, dict):
        for k, v in value.items():
            kl = str(k).lower()
            path = f"{field}.{k}" if field else str(k)
            if kl in _BINARY_KEYS:
                findings.append(RedactionFinding("binary_key", path))
                continue
            if kl in _AUTH_KEYS:
                findings.append(RedactionFinding("auth_key", path))
                continue
            if kl in _PRIVATE_KEYS:
                findings.append(RedactionFinding("private_key", path))
                continue
            _scan_value(v, path, findings, depth + 1)
    elif isinstance(value, list):
        # Phase 6.4C2-B0.2：list 元素的 base64 豁免由 _scan_string 依欄位名處理
        for i, v in enumerate(value):
            _scan_value(v, f"{field}[{i}]", findings, depth + 1)
    elif isinstance(value, bytes):
        findings.append(RedactionFinding("bytes_value", field))
    elif isinstance(value, str):
        _scan_string(value, field, findings)


def scan_redaction_issues(payload: Any) -> list[RedactionFinding]:
    """掃描待匿名化 payload（dict/list 遞迴），回傳全部發現。"""
    findings: list[RedactionFinding] = []
    _scan_value(payload, "", findings)
    return findings


# ---------------------------------------------------------------
# Review 決策（Reviewer A/B 比較）
# ---------------------------------------------------------------
REVIEW_COMPARE_FIELDS = (
    "expected_items",
    "seller_price",
    "currency",
    "wear",
    "stattrak",
    "item_image_indexes",
    "expected_raw_vision_safe",
    "expected_safe_for_production",
    "image_kind",
    "should_create_price",
    "role",
)


def compare_review_annotations(review_a: dict, review_b: dict) -> dict:
    """比較 reviewer A/B 標註，回傳 per-field 結果 + 整體決策。

    結果種類：exact_match / semantic_match / mismatch / missing_on_a / missing_on_b

    - 所有關鍵欄位一致 → double_review
    - 任一關鍵欄位不一致 → disputed
    - 不得自動選擇 A 或 B 作為正確答案
    """
    results: dict[str, str] = {}
    disputed_fields: list[str] = []
    for f in REVIEW_COMPARE_FIELDS:
        if f not in review_a and f not in review_b:
            results[f] = "missing_on_both"
            continue
        if f not in review_a:
            results[f] = "missing_on_a"
            disputed_fields.append(f)
            continue
        if f not in review_b:
            results[f] = "missing_on_b"
            disputed_fields.append(f)
            continue
        va = review_a[f]
        vb = review_b[f]
        if va == vb:
            results[f] = "exact_match"
        elif _semantic_equal(va, vb):
            results[f] = "semantic_match"
        else:
            results[f] = "mismatch"
            disputed_fields.append(f)
    decision = ("double_review" if not disputed_fields else "disputed")
    return {
        "field_results": results,
        "disputed_fields": disputed_fields,
        "decision": decision,
    }


def _semantic_equal(a: Any, b: Any) -> bool:
    """語意相等：數字字串 vs 數字、None vs 空、list 排序等價（set 語意）。"""
    if a is None and b in ("", None, []):
        return True
    if b is None and a in ("", None, []):
        return True
    if isinstance(a, (int, float)) and isinstance(b, str):
        try:
            return float(a) == float(b)
        except ValueError:
            return False
    if isinstance(b, (int, float)) and isinstance(a, str):
        return _semantic_equal(b, a)
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return False
        return sorted(map(str, a)) == sorted(map(str, b))
    return False


def compute_final_ground_truth_hash(final_gt: dict) -> str:
    """deterministic hash：sorted keys + compact JSON。"""
    canonical = json.dumps(final_gt, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def compute_reviewer_inputs_hash(review_a: dict, review_b: dict) -> str:
    return compute_final_ground_truth_hash({"a": review_a, "b": review_b})


# ---------------------------------------------------------------
# 進 anonymized_real / readiness 的兩道 gate（Phase 6.4C2-A.2）
# ---------------------------------------------------------------
VALID_REVIEW_STATUSES = frozenset(
    {"single_review", "disputed", "double_review"})


def can_ingest_as_anonymized_real(
    *,
    provenance: str,
    authorization: str,
    redaction_version: str | None,
    privacy_error_count: int,
    has_image_bytes: bool,
    has_http: bool,
    has_private_fields: bool,
) -> tuple[bool, list[str]]:
    """Gate 1：provenance/authorization/redaction/privacy 合格 → 可 ingest。

    此 gate 不管 review status（single/disputed 仍可 ingest，
    但不得進 readiness）。
    """
    from alkaid_cs2.evaluation.intake_models import can_mark_anonymized_real
    return can_mark_anonymized_real(
        provenance=provenance,
        authorization=authorization,
        redaction_version=redaction_version,
        privacy_error_count=privacy_error_count,
        has_image_bytes=has_image_bytes,
        has_http=has_http,
        has_private_fields=has_private_fields,
    )


def can_enter_real_readiness(
    *,
    review_status: str | None,
) -> tuple[bool, list[str]]:
    """Gate 2：只有 double_review 可進 readiness。

    - review_status 必須是 single_review / disputed / double_review（其他拒絕）
    - 只有 double_review → True
    """
    reasons: list[str] = []
    if review_status not in VALID_REVIEW_STATUSES:
        reasons.append(f"invalid_review_status:{review_status!r}")
        return False, reasons
    if review_status != "double_review":
        reasons.append(f"review_status_not_double:{review_status}")
        return False, reasons
    return True, []


# ---------------------------------------------------------------
# Image hash schema（Phase 6.4C2-A.2）
# ---------------------------------------------------------------
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_image_hashes(
    *,
    original_image_hashes: list,
    redacted_image_hashes: list,
    image_count: int,
) -> list[str]:
    """驗證 image hash 清單：

    - 必須 list[str]
    - 每個為 64 位小寫 hex SHA-256
    - 不允許重複
    - 數量規則與 image_count 一致
    - image_count=0 → 兩清單必須空
    - redacted hash 數不得大於 image_count
    """
    errors: list[str] = []
    if not isinstance(original_image_hashes, list) or \
            not isinstance(redacted_image_hashes, list):
        return ["hashes_not_list"]
    for label, hashes in (("original", original_image_hashes),
                          ("redacted", redacted_image_hashes)):
        for h in hashes:
            if not isinstance(h, str) or not _SHA256_RE.match(h):
                # Phase 6.4C2-A.6：錯誤訊息不含原始值（避免 CLI 回顯）
                errors.append(f"{label}_invalid_hash")
                break
        if len(hashes) != len(set(hashes)):
            errors.append(f"{label}_duplicate_hash")
    if image_count == 0:
        if original_image_hashes or redacted_image_hashes:
            errors.append("image_count_zero_hashes_nonempty")
    else:
        if len(original_image_hashes) != image_count:
            errors.append(
                f"original_hash_count_mismatch:{len(original_image_hashes)}"
                f"!={image_count}")
        if len(redacted_image_hashes) > image_count:
            errors.append(
                f"redacted_hash_exceeds_image_count:{len(redacted_image_hashes)}"
                f">{image_count}")
    return errors
