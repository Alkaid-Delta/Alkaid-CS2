"""
test_price_candidate.py — PriceCandidate 領域模型測試（Phase 2）

驗證 Blueprint §5.4 與 Phase 2 規格：
- confidence 0.0-1.0
- evidence 非空白
- text_start/text_end 非負、text_end >= text_start
- associated_item_index 非負
- Money 保持 immutable
"""
import sys
import os
from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.price import Money  # noqa: E402
from alkaid_cs2.domain.price_candidate import (  # noqa: E402
    PriceCandidate,
    PriceSource,
    PriceType,
)


def make_candidate(**overrides) -> PriceCandidate:
    base = dict(
        money=Money(Decimal("5000"), Currency.TWD),
        price_type=PriceType.SELLER_ASK,
        source=PriceSource.TEXT,
        evidence="售5000",
        confidence=0.9,
        text_start=0,
        text_end=4,
    )
    base.update(overrides)
    return PriceCandidate(**base)


# ---------------------------------------------------------------
# 1. confidence < 0 → raise
# ---------------------------------------------------------------
def test_confidence_below_zero_raises():
    with pytest.raises(ValueError):
        make_candidate(confidence=-0.1)


# ---------------------------------------------------------------
# 2. confidence > 1 → raise
# ---------------------------------------------------------------
def test_confidence_above_one_raises():
    with pytest.raises(ValueError):
        make_candidate(confidence=1.01)


# ---------------------------------------------------------------
# 3. 空 evidence → raise
# ---------------------------------------------------------------
def test_empty_evidence_raises():
    with pytest.raises(ValueError):
        make_candidate(evidence="")
    with pytest.raises(ValueError):
        make_candidate(evidence="   ")


# ---------------------------------------------------------------
# 4. 負 text_start → raise
# ---------------------------------------------------------------
def test_negative_text_start_raises():
    with pytest.raises(ValueError):
        make_candidate(text_start=-1)


# ---------------------------------------------------------------
# 5. text_end < text_start → raise
# ---------------------------------------------------------------
def test_text_end_before_start_raises():
    with pytest.raises(ValueError):
        make_candidate(text_start=5, text_end=3)


# ---------------------------------------------------------------
# 6. associated_item_index < 0 → raise
# ---------------------------------------------------------------
def test_negative_item_index_raises():
    with pytest.raises(ValueError):
        make_candidate(associated_item_index=-1)


# ---------------------------------------------------------------
# 7. image_index < 0 → raise
# ---------------------------------------------------------------
def test_negative_image_index_raises():
    with pytest.raises(ValueError):
        make_candidate(image_index=-1)


# ---------------------------------------------------------------
# 8. 正常建立 PriceCandidate
# ---------------------------------------------------------------
def test_valid_candidate():
    c = make_candidate()
    assert c.money.amount == Decimal("5000")
    assert c.money.currency is Currency.TWD
    assert c.price_type is PriceType.SELLER_ASK
    assert c.source is PriceSource.TEXT
    assert c.evidence == "售5000"
    assert c.confidence == 0.9
    assert c.text_start == 0
    assert c.text_end == 4
    assert c.converted is None  # 不自動換算


# ---------------------------------------------------------------
# 9. Money 保持 immutable（PriceCandidate 不修改原始 Money）
# ---------------------------------------------------------------
def test_money_stays_immutable():
    m = Money(Decimal("5000"), Currency.TWD)
    c = PriceCandidate(
        money=m,
        price_type=PriceType.REFERENCE,
        source=PriceSource.TEXT,
        evidence="NT$5000",
        confidence=0.8,
    )
    with pytest.raises(FrozenInstanceError):
        c.money.amount = Decimal("9999")  # type: ignore[misc]
    # Money 物件本身也不可被 candidate 覆寫
    assert m.amount == Decimal("5000")


# ---------------------------------------------------------------
# 10. 型別錯誤 → raise
# ---------------------------------------------------------------
def test_wrong_types_raise():
    with pytest.raises(TypeError):
        make_candidate(money="5000")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_candidate(price_type="selling")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_candidate(source="text")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 11. converted 錯誤型別 → raise
# ---------------------------------------------------------------
def test_converted_wrong_type_raises():
    from alkaid_cs2.domain.price import ConvertedMoney
    from alkaid_cs2.services.currency import CurrencyService

    svc = CurrencyService(rmb_to_twd=Decimal("4.5"), usd_to_rmb=Decimal("7.2"),
                          rate_source="test")
    m = Money(Decimal("5000"), Currency.TWD)
    good = svc.to_twd(m)
    # 正確型別可接受
    assert make_candidate(converted=good).converted is good
    # 錯誤型別 raise
    with pytest.raises(TypeError):
        make_candidate(converted="4500")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_candidate(converted=4500)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 12. confidence=True → raise（bool 是 int 子類，必須拒絕）
# ---------------------------------------------------------------
def test_confidence_bool_raises():
    with pytest.raises(TypeError):
        make_candidate(confidence=True)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        make_candidate(confidence=False)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 13. confidence=NaN → raise
# ---------------------------------------------------------------
def test_confidence_nan_raises():
    with pytest.raises(ValueError):
        make_candidate(confidence=float("nan"))


# ---------------------------------------------------------------
# 14. confidence=Infinity / -Infinity → raise
# ---------------------------------------------------------------
def test_confidence_infinity_raises():
    with pytest.raises(ValueError):
        make_candidate(confidence=float("inf"))
    with pytest.raises(ValueError):
        make_candidate(confidence=float("-inf"))


# ---------------------------------------------------------------
# 15. confidence 為整數可接受（int 是合法型別）
# ---------------------------------------------------------------
def test_confidence_int_ok():
    c = make_candidate(confidence=1)
    assert c.confidence == 1.0
    c2 = make_candidate(confidence=0)
    assert c2.confidence == 0.0
