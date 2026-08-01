"""test_real_case_redaction.py — 匿名化轉換測試（Phase 6.4C2-A）"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
    scan_redaction_issues,
)
from alkaid_cs2.evaluation.redaction import redact_real_case_input  # noqa: E402


def _redact(raw, **kw):
    base = dict(case_id="real_001", redaction_version="v1.0",
                provenance="user_supplied_real", authorization="user_supplied")
    base.update(kw)
    return redact_real_case_input(raw, **base)


# ---------------------------------------------------------------
# 十一、Privacy 與資料治理測試（redaction 層）
# ---------------------------------------------------------------
def test_nested_token_rejected():
    findings = scan_redaction_issues({"data": {"nested": {"token": "sk-abc"}}})
    assert any(f.code == "auth_key" for f in findings)


def test_nested_cookie_rejected():
    findings = scan_redaction_issues({"headers": {"cookie": "session=1"}})
    assert any(f.code == "auth_key" for f in findings)


def test_nested_sender_rejected():
    findings = scan_redaction_issues({"message": {"sender": "王小明"}})
    assert any(f.code == "private_key" for f in findings)


def test_email_rejected():
    findings = scan_redaction_issues({"raw_text": "聯絡 aaa@bbb.com"})
    assert any(f.code == "email" for f in findings)


def test_taiwan_mobile_rejected():
    findings = scan_redaction_issues({"raw_text": "電話 0912345678"})
    assert any(f.code == "tw_mobile" for f in findings)


def test_facebook_url_rejected():
    findings = scan_redaction_issues({"raw_text": "https://www.facebook.com/abc"})
    assert any(f.code == "facebook_url" for f in findings)


def test_fbcdn_url_rejected():
    findings = scan_redaction_issues({"raw_text": "https://scontent.xx.fbcdn.net/v/t1"})
    assert any(f.code == "facebook_url" for f in findings)


def test_base64_rejected():
    findings = scan_redaction_issues({"raw_text": "data:image/png;base64,iVBORw0KGgo"})
    assert any(f.code == "base64_like" for f in findings)


def test_bytes_rejected():
    findings = scan_redaction_issues({"data": b"\x89PNG\r\n"})
    assert any(f.code == "bytes_value" for f in findings)


def test_exif_like_fields_rejected():
    findings = scan_redaction_issues({"image": {"exif": {"GPSLatitude": 25.0}}})
    assert any(f.code == "binary_key" for f in findings)


def test_local_path_rejected():
    findings = scan_redaction_issues({"raw_text": r"C:\Users\user\Desktop\a.png"})
    assert any(f.code == "local_path" for f in findings)


# ---------------------------------------------------------------
# 匿名化轉換行為
# ---------------------------------------------------------------
def test_redact_removes_sensitive_and_marks_anonymous():
    raw = {"raw_text": "售 A 算5000 聯絡 aaa@bbb.com 電話0912345678",
           "sender": "王小明", "token": "sk-x",
           "image_bytes": b"123"}
    draft, reasons = _redact(raw)
    assert draft["author"] == "anonymous"
    assert draft["link"] == "redacted://real_001"
    assert draft["redaction_version"] == "v1.0"
    assert draft["ground_truth_review_status"] == "single_review"
    assert "王小明" not in draft["raw_text"]
    assert "aaa@bbb.com" not in draft["raw_text"]
    assert "0912345678" not in draft["raw_text"]
    assert "sender" not in draft and "token" not in draft
    assert "image_bytes" not in draft
    assert draft["source"] == "anonymized_real"


def test_redact_provenance_fail_keeps_draft():
    raw = {"raw_text": "售 A 算5000"}
    draft, reasons = _redact(raw, provenance="agent_generated")
    assert draft["source"] == "unverified_real_draft"
    assert any("provenance_invalid" in r for r in reasons)


def test_redact_does_not_invent_ground_truth():
    # 不自動補商品/價格/幣別
    raw = {"raw_text": "售 A 算5000"}
    draft, reasons = _redact(raw)
    assert "expected_items" not in draft
    assert "seller_price" not in draft


def test_redact_keeps_notes_non_sensitive():
    raw = {"raw_text": "售 A 算5000", "notes": "賣家已讀不回"}
    draft, reasons = _redact(raw)
    assert draft.get("notes") == "賣家已讀不回"
