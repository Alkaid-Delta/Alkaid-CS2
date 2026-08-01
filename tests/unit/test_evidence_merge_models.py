"""
test_evidence_merge_models.py — EvidenceConflict / MergedEvidenceResult 測試（Phase 6.3B）
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.evidence_merge import (  # noqa: E402
    ConflictType,
    EvidenceConflict,
    MergedEvidenceResult,
)
from alkaid_cs2.domain.image_evidence import (  # noqa: E402
    ImageEvidence,
    ImageEvidenceSource,
    ImageKind,
    ImagePlatform,
)
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus  # noqa: E402


def make_conflict(**overrides) -> EvidenceConflict:
    base = dict(
        conflict_type=ConflictType.WEAR_CONFLICT,
        reason="wear 衝突: text=FT image=MW",
        severity="warning",
    )
    base.update(overrides)
    return EvidenceConflict(**base)


def make_parsed_post():
    return ParsedPost(post_id="p1", raw_text="售 紅線 5000",
                      parse_status=ParseStatus.OK, source="test")


def make_evidence():
    return ImageEvidence(
        image_index=0, image_url="https://x/1.jpg", image_hash=None,
        image_kind=ImageKind.SINGLE_ITEM, platform=ImagePlatform.FACEBOOK,
        source=ImageEvidenceSource.VISION, raw_result={}, confidence=0.8,
    )


def make_result(**overrides) -> MergedEvidenceResult:
    base = dict(
        parsed_post=make_parsed_post(),
        image_evidence=[make_evidence()],
        conflicts=[make_conflict()],
        warnings=["ok"],
        text_item_count=1, image_item_count=1, merged_item_count=2,
        text_price_count=1, image_price_count=1, merged_price_count=2,
    )
    base.update(overrides)
    return MergedEvidenceResult(**base)


# ---------------------------------------------------------------
# 1. 正常 conflict
# ---------------------------------------------------------------
def test_valid_conflict():
    c = make_conflict()
    assert c.conflict_type is ConflictType.WEAR_CONFLICT
    assert c.severity == "warning"
    assert c.resolved is False


# ---------------------------------------------------------------
# 2. 錯誤 conflict_type
# ---------------------------------------------------------------
def test_invalid_conflict_type():
    with pytest.raises(TypeError):
        make_conflict(conflict_type="wear_conflict")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 3-4. indexes 驗證
# ---------------------------------------------------------------
def test_negative_indexes():
    with pytest.raises(ValueError):
        make_conflict(text_item_index=-1)


def test_bool_indexes():
    with pytest.raises(TypeError):
        make_conflict(image_index=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 5. blank reason
# ---------------------------------------------------------------
def test_blank_reason():
    with pytest.raises(ValueError):
        make_conflict(reason="")


# ---------------------------------------------------------------
# 6. invalid severity
# ---------------------------------------------------------------
def test_invalid_severity():
    with pytest.raises(ValueError):
        make_conflict(severity="critical")


# ---------------------------------------------------------------
# 7. resolved 錯誤型別
# ---------------------------------------------------------------
def test_resolved_wrong_type():
    with pytest.raises(TypeError):
        make_conflict(resolved=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 8. 正常 MergedEvidenceResult
# ---------------------------------------------------------------
def test_valid_merged_result():
    r = make_result()
    assert isinstance(r.parsed_post, ParsedPost)
    assert r.merged_item_count == 2
    assert r.merged_price_count == 2


# ---------------------------------------------------------------
# 9-11. 錯誤型別
# ---------------------------------------------------------------
def test_wrong_parsed_post_type():
    with pytest.raises(TypeError):
        make_result(parsed_post="x")  # type: ignore[arg-type]


def test_wrong_image_evidence_type():
    with pytest.raises(TypeError):
        make_result(image_evidence=["x"])  # type: ignore[list-item]


def test_wrong_conflict_type():
    with pytest.raises(TypeError):
        make_result(conflicts=["x"])  # type: ignore[list-item]


# ---------------------------------------------------------------
# 12. count 負數
# ---------------------------------------------------------------
def test_count_negative():
    with pytest.raises(ValueError):
        make_result(merged_item_count=-1)


# ---------------------------------------------------------------
# 13. warnings 空白
# ---------------------------------------------------------------
def test_warnings_blank():
    with pytest.raises(ValueError):
        make_result(warnings=["ok", ""])


# ---------------------------------------------------------------
# 14. mutable 欄位獨立
# ---------------------------------------------------------------
def test_mutable_fields_independent():
    r1 = make_result(warnings=["w1"])
    r2 = make_result(warnings=["w2"])
    r1.warnings.append("mutated")
    assert "mutated" not in r2.warnings
    assert r1.conflicts is not r2.conflicts
    assert r1.image_evidence is not r2.image_evidence
