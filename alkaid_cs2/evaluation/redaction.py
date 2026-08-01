# -*- coding: utf-8 -*-
"""
redaction.py — 真實案例匿名化轉換（Phase 6.4C2-A）

- 接收本機輸入 dict → 產生可進 fixtures 的匿名化 draft
- 全部規則 deterministic、不呼叫 LLM、不連網
- 不自動補商品、價格、幣別或 Ground Truth（欄位保留給 reviewer）
- 匿名化後：author="anonymous"、link="redacted://<case_id>"、
  source="anonymized_real"（僅當全部 gate 通過）；否則 source="unverified_real_draft"
"""
from __future__ import annotations

import re
from typing import Any

from alkaid_cs2.evaluation.intake_models import (
    can_mark_anonymized_real,
)
from alkaid_cs2.evaluation.intake_validation import (
    scan_redaction_issues,
)

_UNVERIFIED_DRAFT = "unverified_real_draft"

# 待清除的頂層鍵（真實欄位 → 不進入 fixtures）
_STRIP_TOP_KEYS = frozenset({
    "sender", "recipient", "author_id", "facebook_id", "profile_url",
    "user_id", "account_id", "phone", "email", "real_name", "full_name",
    "authorization", "cookie", "cookies", "token", "api_key", "apikey",
    "access_token", "refresh_token", "session", "secret", "password",
    "raw_bytes", "image_bytes", "bytes", "image_base64", "base64",
    "data_url", "thumbnail", "exif", "exif_data",
})


def _redact_string(value: str) -> str:
    """deterministic 替換：email/手機/長 ID/URL/本機路徑 → 標記。"""
    v = value
    v = re.sub(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
               "[REDACTED_EMAIL]", v)
    v = re.sub(r"(?:09\d{8}|8869\d{8}|\+8869\d{8})",
               "[REDACTED_PHONE]", v)
    v = re.sub(r"\b\d{10,}\b", "[REDACTED_ID]", v)
    v = re.sub(r"https?://[^\s\u4e00-\u9fff，。、；：""''（）]+",
               "[REDACTED_URL]", v)
    v = re.sub(r"(?:[A-Za-z]:[\\/]|[/\\]{1,2}[A-Za-z0-9_.-]+[\\/])",
               "[REDACTED_PATH]", v)
    return v


def _redact_value(value: Any, key: str, depth: int = 0) -> Any:
    """遞迴清除/替換敏感內容。bytes → 移除；敏感鍵 → 移除；str → redact。"""
    if depth > 20:
        return "[REDACTED_DEPTH]"
    if isinstance(value, bytes):
        return None  # bytes 一律移除（不轉字串）
    if isinstance(value, dict):
        out = {}
        for k, v in value.items():
            kl = str(k).lower()
            if kl in _STRIP_TOP_KEYS:
                continue  # 敏感欄位整個移除
            redacted = _redact_value(v, kl, depth + 1)
            if redacted is not None:  # bytes → None → 移除
                out[k] = redacted
        return out
    if isinstance(value, list):
        out = []
        for v in value:
            redacted = _redact_value(v, key, depth + 1)
            if redacted is not None:
                out.append(redacted)
        return out
    if isinstance(value, str):
        return _redact_string(value)
    return value


def redact_real_case_input(
    raw: dict,
    *,
    case_id: str,
    redaction_version: str,
    provenance: str,
    authorization: str,
) -> tuple[dict, list[str]]:
    """匿名化本機輸入，回傳 (draft_dict, reasons)。

    draft 結構：
    {
      "case_id", "source": "anonymized_real" | "unverified_real_draft",
      "author": "anonymous", "link": "redacted://<case_id>",
      "raw_text": <redacted 後文字>,
      "redaction_version", "ground_truth_review_status": "single_review",
      "images": [...], "expected_items": [...], 等 reviewer 標註欄位
    }

    不會自動補商品/價格/幣別/Ground Truth。
    """
    reasons: list[str] = []
    redacted = _redact_value(raw, "")
    if not isinstance(redacted, dict):
        raise ValueError("輸入必須是 JSON object")

    findings = scan_redaction_issues(redacted)
    errors = [f for f in findings if f.severity == "error"]
    has_image_bytes = any(f.code == "bytes_value" or f.code == "binary_key"
                          for f in findings)
    has_http = any(f.code == "http_url" or f.code == "facebook_url"
                   for f in findings)
    has_private = any(f.code in ("auth_key", "private_key", "email",
                                 "tw_mobile", "long_numeric_id")
                      for f in findings)

    ok, gate_reasons = can_mark_anonymized_real(
        provenance=provenance,
        authorization=authorization,
        redaction_version=redaction_version,
        privacy_error_count=len(errors),
        has_image_bytes=has_image_bytes,
        has_http=has_http,
        has_private_fields=has_private,
    )
    reasons.extend(gate_reasons)
    for f in findings:
        reasons.append(f"privacy:{f.code}:{f.field}")

    draft: dict[str, Any] = {
        "case_id": case_id,
        "source": "anonymized_real" if ok else _UNVERIFIED_DRAFT,
        "author": "anonymous",
        "link": f"redacted://{case_id}",
        "redaction_version": redaction_version,
        "ground_truth_review_status": "single_review",
        "notes": (redacted.get("notes") or ""),
    }
    # Phase 6.4C2-A.2：draft 不得保留任何預載 Ground Truth——
    # 以下欄位全部由 reviewer workflow 建立
    # （raw_text 已 redact；images 僅保留非敏感 metadata）
    for key in ("raw_text", "images"):
        if key in redacted:
            draft[key] = redacted[key]
    return draft, reasons
