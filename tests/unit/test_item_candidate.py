"""
test_item_candidate.py — ItemCandidate 領域模型測試（Phase 3）

驗證：
- confidence/score 有限、0-1、拒絕 bool
- original_text / parser 非空白
- 位置非負、match_end >= match_start
- linked_price_indexes 無負數無重複
- verified=True 需名稱
- unresolved 候選可保留 validation_error
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402


def make_candidate(**overrides) -> ItemCandidate:
    base = dict(
        market_hash_name="AK-47 | Redline (Field-Tested)",
        weapon="AK-47",
        skin="Redline",
        wear="Field-Tested",
        stattrak=False,
        role=ItemRole.SELLING,
        original_text="售 AK-47 | 红线 久经沙场 5000",
        matched_key="AK-47 | 红线",
        match_start=2,
        match_end=12,
        parser="item_parser",
        evidence=ItemEvidence.DICT_FULL,
        confidence=0.95,
        score=100.0,
        verified=False,
    )
    base.update(overrides)
    return ItemCandidate(**base)


# ---------------------------------------------------------------
# 1. confidence < 0 / > 1 → raise
# ---------------------------------------------------------------
def test_confidence_out_of_range():
    with pytest.raises(ValueError):
        make_candidate(confidence=-0.1)
    with pytest.raises(ValueError):
        make_candidate(confidence=1.1)


# ---------------------------------------------------------------
# 2. confidence=True → raise（bool 拒絕）
# ---------------------------------------------------------------
def test_confidence_bool_raises():
    with pytest.raises(TypeError):
        make_candidate(confidence=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 3. confidence=NaN / Infinity → raise
# ---------------------------------------------------------------
def test_confidence_non_finite():
    with pytest.raises(ValueError):
        make_candidate(confidence=float("nan"))
    with pytest.raises(ValueError):
        make_candidate(confidence=float("inf"))
    with pytest.raises(ValueError):
        make_candidate(confidence=float("-inf"))


# ---------------------------------------------------------------
# 4. score=NaN / Infinity → raise
# ---------------------------------------------------------------
def test_score_non_finite():
    with pytest.raises(ValueError):
        make_candidate(score=float("nan"))
    with pytest.raises(ValueError):
        make_candidate(score=float("inf"))


# ---------------------------------------------------------------
# 5. score=True → raise
# ---------------------------------------------------------------
def test_score_bool_raises():
    with pytest.raises(TypeError):
        make_candidate(score=True)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 6. 空 original_text → raise
# ---------------------------------------------------------------
def test_empty_original_text():
    with pytest.raises(ValueError):
        make_candidate(original_text="")
    with pytest.raises(ValueError):
        make_candidate(original_text="   ")


# ---------------------------------------------------------------
# 7. 空 parser → raise
# ---------------------------------------------------------------
def test_empty_parser():
    with pytest.raises(ValueError):
        make_candidate(parser="")


# ---------------------------------------------------------------
# 8. 負 match_start → raise
# ---------------------------------------------------------------
def test_negative_match_start():
    with pytest.raises(ValueError):
        make_candidate(match_start=-1)


# ---------------------------------------------------------------
# 9. match_end < match_start → raise
# ---------------------------------------------------------------
def test_match_end_before_start():
    with pytest.raises(ValueError):
        make_candidate(match_start=5, match_end=3)


# ---------------------------------------------------------------
# 10. 負 image_index → raise
# ---------------------------------------------------------------
def test_negative_image_index():
    with pytest.raises(ValueError):
        make_candidate(image_index=-1)


# ---------------------------------------------------------------
# 11. linked_price_indexes 有負數 → raise
# ---------------------------------------------------------------
def test_linked_indexes_negative():
    with pytest.raises(ValueError):
        make_candidate(linked_price_indexes=[0, -1])


# ---------------------------------------------------------------
# 12. linked_price_indexes 有重複 → raise
# ---------------------------------------------------------------
def test_linked_indexes_duplicate():
    with pytest.raises(ValueError):
        make_candidate(linked_price_indexes=[1, 1])


# ---------------------------------------------------------------
# 13. verified=True 但 market_hash_name=None → raise
# ---------------------------------------------------------------
def test_verified_requires_name():
    with pytest.raises(ValueError):
        make_candidate(verified=True, market_hash_name=None)


# ---------------------------------------------------------------
# 14. 正常建立候選
# ---------------------------------------------------------------
def test_valid_candidate():
    c = make_candidate()
    assert c.market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert c.weapon == "AK-47"
    assert c.skin == "Redline"
    assert c.wear == "Field-Tested"
    assert c.stattrak is False
    assert c.role is ItemRole.SELLING
    assert c.evidence is ItemEvidence.DICT_FULL
    assert c.confidence == 0.95
    assert c.score == 100.0
    assert c.verified is False
    assert c.linked_price_indexes == []


# ---------------------------------------------------------------
# 15. validation_error 可保留在 verified=False 候選（unresolved）
# ---------------------------------------------------------------
def test_unresolved_keeps_validation_error():
    c = make_candidate(
        verified=False,
        market_hash_name="Fake Skin A",
        validation_error="驗證兩次失敗 (Fake Skin A / Fake Skin B)",
        confidence=0.4,
        score=30.0,
    )
    assert c.verified is False
    assert c.validation_error == "驗證兩次失敗 (Fake Skin A / Fake Skin B)"
    # unresolved 候選即使有 validation_error 也不違反 verified 規則


# ---------------------------------------------------------------
# 16. 型別錯誤 → raise
# ---------------------------------------------------------------
def test_wrong_enum_types():
    with pytest.raises(TypeError):
        make_candidate(role="selling")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_candidate(evidence="dict_full")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 17. matched_text 正常（自動填入 = 原文切片）
# ---------------------------------------------------------------
def test_matched_text_auto_fill():
    c = make_candidate()  # original_text="售 AK-47 | 红线 久经沙场 5000", match 2-11
    assert c.matched_text == "AK-47 | 红线"
    # 位置指向的原文切片
    assert c.original_text[c.match_start:c.match_end] == c.matched_text


# ---------------------------------------------------------------
# 18. matched_text 與原文切片不一致 → ValueError
# ---------------------------------------------------------------
def test_matched_text_mismatch_raises():
    with pytest.raises(ValueError):
        make_candidate(matched_text="AK-47 | 火神")  # 與切片 "AK-47 | 红线" 不同


# ---------------------------------------------------------------
# 19. 有 match_start/end 但 matched_text=None → 自動填入
#     （策略：__post_init__ 自動填入，不要求 parser 手動填）
# ---------------------------------------------------------------
def test_matched_text_none_auto_filled():
    c = make_candidate(matched_text=None)
    assert c.matched_text == "AK-47 | 红线"


# ---------------------------------------------------------------
# 20. match_end 超過 original_text 長度 → ValueError
# ---------------------------------------------------------------
def test_match_end_exceeds_text():
    with pytest.raises(ValueError):
        make_candidate(match_start=10, match_end=999)


# ---------------------------------------------------------------
# 21. 無位置時 matched_text 允許 None
# ---------------------------------------------------------------
def test_matched_text_none_without_position():
    c = make_candidate(match_start=None, match_end=None, matched_text=None)
    assert c.matched_text is None
