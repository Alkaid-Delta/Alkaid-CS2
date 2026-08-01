"""test_evaluation_privacy.py — 隱私掃描測試（Phase 6.4C1）"""
import json
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.models import (  # noqa: E402
    EvaluationCase, EvaluationImage, EvaluationSource, ExpectedImageKind,
    ExpectedItem, GroundTruthReviewStatus,
)
from alkaid_cs2.evaluation.privacy import (  # noqa: E402
    PrivacyFinding, scan_fixture_for_sensitive_data,
)


def _case(**kw):
    base = dict(case_id="t1", source=EvaluationSource.ANONYMIZED_REAL,
                author="anonymous", link="redacted://t1", raw_text="售 A 算5000",
                expected_safe_for_production=True,
                redaction_version="1.0",
                ground_truth_review_status="double_review",
                ground_truth_reviewed_by="reviewer_a")
    base.update(kw)
    rs = base.get("ground_truth_review_status")
    if rs is not None and not isinstance(rs, GroundTruthReviewStatus):
        base["ground_truth_review_status"] = GroundTruthReviewStatus(rs)
    return EvaluationCase(**base)


def _errors(case):
    return [f for f in scan_fixture_for_sensitive_data(case) if f.severity == "error"]


# ================================================================
# 治理欄位
# ================================================================
def test_real_fixture_requires_redaction_version():
    c = _case(redaction_version=None)
    assert any(f.code == "redaction_version_missing" for f in _errors(c))


def test_real_fixture_requires_review_status():
    c = _case(ground_truth_review_status=None)
    assert any(f.code == "review_status_missing" for f in _errors(c))


def test_http_link_rejected():
    c = _case(link="https://example.com/post/123")
    assert any(f.code == "real_link" for f in _errors(c))


def test_facebook_link_rejected():
    c = _case(link="https://www.facebook.com/groups/cs2/posts/123")
    assert any(f.code == "real_link" for f in _errors(c))


# ================================================================
# 敏感資料
# ================================================================
def test_email_detected():
    c = _case(raw_text="聯絡 john.doe@gmail.com 謝謝")
    assert any(f.code == "email" for f in _errors(c))


def test_phone_detected():
    c = _case(raw_text="電話 0912345678 面交")
    assert any(f.code == "phone" for f in _errors(c))


def test_token_detected():
    c = _case(raw_text="key=sk-abcdefgh12345678")
    assert any(f.code == "token" for f in _errors(c))


def test_base64_detected():
    c = _case(raw_text="data:image/png;base64,iVBORw0KGgoAAAANSUhEUg==")
    assert any(f.code == "base64_image" for f in _errors(c))


def test_facebook_id_warning():
    c = _case(raw_text="ID 1000123456789 有興趣")
    assert any(f.code == "facebook_user_id" and f.severity == "warning"
               for f in scan_fixture_for_sensitive_data(c))


def test_anonymous_author_allowed():
    c = _case(author="anonymous")
    assert not any(f.code == "real_author" for f in _errors(c))


def test_real_author_rejected():
    c = _case(author="張三")
    assert any(f.code == "real_author" for f in _errors(c))


def test_private_payload_field_rejected():
    img = EvaluationImage(image_index=0, image_url="redacted://i/0",
                          image_kind=ExpectedImageKind.SINGLE,
                          vision_payload={"type": "single", "items": [],
                                          "author": "真實姓名"})
    c = _case(images=[img])
    assert any(f.code == "private_payload_field" for f in _errors(c))


def test_privacy_warning_reported():
    c = _case(raw_text="ID 1000123456789 有興趣")  # 長數字 → warning
    warnings = [f for f in scan_fixture_for_sensitive_data(c)
                if f.severity == "warning"]
    assert warnings, "warning 必須出現"


def test_privacy_error_blocks_loader():
    # loader 對 error finding 拒絕載入
    import tempfile
    from pathlib import Path
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_case
    data = {
        "case_id": "bad1", "source": "anonymized_real", "author": "anonymous",
        "link": "https://evil.com/x", "raw_text": "售 A 算5000",
        "expected_safe_for_production": True,
        "expected_items": [],
        "redaction_version": "1.0",
        "ground_truth_review_status": "double_review",
    }
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "bad1.json"
        p.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ValueError, match="隱私掃描拒絕載入"):
            load_evaluation_case(p)


def test_nested_sensitive_field_in_payload():
    img = EvaluationImage(image_index=0, image_url="redacted://i/0",
                          image_kind=ExpectedImageKind.CHAT,
                          vision_payload={"type": "chat", "items": [],
                                          "sender": "好友A"})
    c = _case(images=[img])
    assert any(f.code == "private_payload_field" for f in _errors(c))
