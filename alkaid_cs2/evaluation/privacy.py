# -*- coding: utf-8 -*-
"""
privacy.py — 匿名化真實案例隱私掃描器（Phase 6.4C1）

scan_fixture_for_sensitive_data(case) -> list[PrivacyFinding]

severity：
- error：禁止進 dataset（loader 拒絕載入）
- warning：允許但必須出現在報告
"""
import base64
import re
from dataclasses import dataclass, field

from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource


@dataclass
class PrivacyFinding:
    code: str
    field: str
    severity: str  # "error" | "warning"
    message: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("code 必須非空 str")
        if not isinstance(self.field, str) or not self.field.strip():
            raise ValueError("field 必須非空 str")
        if self.severity not in ("error", "warning"):
            raise ValueError(f"severity 必須 error/warning，收到 {self.severity!r}")
        if not isinstance(self.message, str) or not self.message.strip():
            raise ValueError("message 必須非空 str")


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?<!\d)(09\d{8}|\+8869\d{8})(?!\d)")
_FB_ID_RE = re.compile(r"(?<!\d)(\d{10,})(?!\d)")  # 長數字型 FB ID
_BASE64_IMG_RE = re.compile(r"data:image/[a-z+]+;base64,", re.IGNORECASE)
_TOKEN_RE = re.compile(r"\b(?:sk-|ghp_|gho_|xox[bap]?-|EAAG|ya29\.)[A-Za-z0-9_-]{8,}\b")
_AUTH_RE = re.compile(r"\b(?:authorization|bearer|cookie|api[-_]?key)\b",
                      re.IGNORECASE)

_REDACTED_LINK_PREFIXES = ("redacted://", "fixture://")


def _scan_string(value: str, field: str, findings: list[PrivacyFinding]) -> None:
    if not value:
        return
    if "http://" in value or "https://" in value:
        findings.append(PrivacyFinding(
            "http_url", field, "error", "發現 http/https URL（真實連結外洩）"))
    if "facebook.com" in value or "fbcdn" in value.lower():
        findings.append(PrivacyFinding(
            "facebook_url", field, "error", "發現 facebook/fbcdn 網域"))
    if _EMAIL_RE.search(value):
        findings.append(PrivacyFinding(
            "email", field, "error", "發現 email pattern"))
    if _PHONE_RE.search(value):
        findings.append(PrivacyFinding(
            "phone", field, "error", "發現台灣手機號碼 pattern"))
    if _FB_ID_RE.search(value):
        findings.append(PrivacyFinding(
            "facebook_user_id", field, "warning",
            "發現長數字（疑似 FB user ID）"))
    if _BASE64_IMG_RE.search(value):
        findings.append(PrivacyFinding(
            "base64_image", field, "error", "發現 base64 圖片前綴（不得存圖）"))
    if _TOKEN_RE.search(value):
        findings.append(PrivacyFinding(
            "token", field, "error", "發現疑似 token/API key pattern"))
    if _AUTH_RE.search(value):
        findings.append(PrivacyFinding(
            "auth_keyword", field, "warning",
            "發現 authorization/bearer/cookie/api_key 關鍵字"))


_SENSITIVE_KEYS = ("author", "user_name", "facebook_id", "fb_id", "profile_url",
                   "sender", "recipient", "account", "phone", "email",
                   "raw_bytes", "image_bytes", "image_base64", "data_url",
                   "authorization", "cookie", "token", "api_key")
_BINARY_KEYS = ("raw_bytes", "image_bytes", "bytes", "image_base64", "base64",
                "data_url")


def _scan_value(value: object, field: str, findings: list[PrivacyFinding],
                depth: int = 0) -> None:
    """遞迴掃描 dict/list/str/bytes（Phase 6.4C1.1）。"""
    if depth > 20:
        return
    if isinstance(value, bytes):
        findings.append(PrivacyFinding(
            "image_bytes", field, "error", "發現 bytes（不得保存）"))
        return
    if isinstance(value, str):
        _scan_string(value, field, findings)
        return
    if isinstance(value, dict):
        for k, v in value.items():
            k_field = f"{field}.{k}"
            if isinstance(k, str) and k.lower() in _SENSITIVE_KEYS and v:
                findings.append(PrivacyFinding(
                    "private_payload_field", k_field, "error",
                    f"payload 含私人欄位 {k}"))
            if isinstance(k, str) and k.lower() in _BINARY_KEYS:
                findings.append(PrivacyFinding(
                    "binary_field", k_field, "error",
                    f"payload 含 binary 欄位 {k}（不得保存）"))
            _scan_value(v, k_field, findings, depth + 1)
        return
    if isinstance(value, list):
        for i, v in enumerate(value):
            _scan_value(v, f"{field}[{i}]", findings, depth + 1)


def _find_strings(obj: object, out: list[str]) -> None:
    if isinstance(obj, str):
        out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(k, str):
                out.append(k)
            _find_strings(v, out)
    elif isinstance(obj, list):
        for v in obj:
            _find_strings(v, out)


def scan_fixture_for_sensitive_data(case: EvaluationCase) -> list[PrivacyFinding]:
    """掃描單一案例。error → 禁止進 dataset。"""
    findings: list[PrivacyFinding] = []

    # 1. author：anonymized_real 只允許 anonymous
    if case.source == EvaluationSource.ANONYMIZED_REAL and \
            case.author not in ("anonymous", "synthetic"):
        findings.append(PrivacyFinding(
            "real_author", "author", "error",
            f"anonymized_real author 必須 anonymous/synthetic，收到 {case.author!r}"))

    # 2. link：不得 http/https/facebook；必須 redacted:// 或 fixture://
    if case.link:
        if case.link.startswith(("http://", "https://")):
            findings.append(PrivacyFinding(
                "real_link", "link", "error",
                "link 不得是 http/https URL"))
        elif not case.link.startswith(_REDACTED_LINK_PREFIXES):
            findings.append(PrivacyFinding(
                "link_not_redacted", "link", "warning",
                f"link 建議 redacted:// 或 fixture:// 前綴（收到 {case.link[:40]!r}）"))

    # 3. raw_text / notes
    _scan_string(case.raw_text, "raw_text", findings)
    if case.notes:
        _scan_string(case.notes, "notes", findings)

    # 4. images（遞迴掃 payload）
    for img in case.images:
        _scan_string(img.image_url, f"images[{img.image_index}].image_url", findings)
        payload = getattr(img, "vision_payload", None)
        if payload is not None:
            _scan_value(payload, f"images[{img.image_index}].payload", findings)

    # 5. image bytes
    for img in case.images:
        raw = getattr(img, "_raw_bytes", None)
        if raw is not None:
            findings.append(PrivacyFinding(
                "image_bytes", f"images[{img.image_index}]", "error",
                "案例含原始圖片 bytes（不得 commit）"))

    # 6. redaction / review 治理欄位
    if case.source == EvaluationSource.ANONYMIZED_REAL:
        if not case.redaction_version:
            findings.append(PrivacyFinding(
                "redaction_version_missing", "redaction_version", "error",
                "anonymized_real 必填 redaction_version"))
        if case.ground_truth_review_status is None:
            findings.append(PrivacyFinding(
                "review_status_missing", "ground_truth_review_status", "error",
                "anonymized_real 必填 ground_truth_review_status"))
        if case.ground_truth_reviewed_by and \
                case.ground_truth_reviewed_by not in ("reviewer_a", "reviewer_b"):
            findings.append(PrivacyFinding(
                "real_reviewer_name", "ground_truth_reviewed_by", "error",
                "reviewer 必須 reviewer_a/reviewer_b（不得真實姓名）"))

    return findings


def has_privacy_errors(case: EvaluationCase) -> bool:
    return any(f.severity == "error"
               for f in scan_fixture_for_sensitive_data(case))


def decode_image_bytes(b64: str) -> bytes:
    """decode data URL / base64（供測試用；正式 fixture 不存 bytes）。"""
    if "," in b64:
        b64 = b64.split(",", 1)[1]
    return base64.b64decode(b64)
