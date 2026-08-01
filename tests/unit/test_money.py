"""
test_money.py — Money / ConvertedMoney / CurrencyService 單元測試（Phase 1）

驗證 Blueprint §8 Currency Policy：
- Money immutable、Decimal、禁止負數
- CurrencyService 唯一換算處、禁止重複換算、禁止 float
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
from alkaid_cs2.domain.price import ConvertedMoney, Money  # noqa: E402
from alkaid_cs2.services.currency import CurrencyService  # noqa: E402


def make_service() -> CurrencyService:
    """測試用服務：RMB×4.5、USD×7.2（與 production 一致）"""
    return CurrencyService(
        rmb_to_twd=Decimal("4.5"),
        usd_to_rmb=Decimal("7.2"),
        rate_source="test-fixed",
    )


# ---------------------------------------------------------------
# 1. TWD 不換算（rate=1）
# ---------------------------------------------------------------
def test_twd_no_conversion():
    svc = make_service()
    m = Money(Decimal("100"), Currency.TWD)
    result = svc.to_twd(m)

    assert result.twd_amount == Decimal("100")
    assert result.rate_used == Decimal("1")
    assert result.original is m  # 原始物件保留
    assert result.original.amount == Decimal("100")
    assert result.original.currency is Currency.TWD


# ---------------------------------------------------------------
# 2. 1000 RMB -> 4500 TWD
# ---------------------------------------------------------------
def test_rmb_conversion():
    svc = make_service()
    m = Money(Decimal("1000"), Currency.RMB)
    result = svc.to_twd(m)

    assert result.twd_amount == Decimal("4500")
    assert result.rate_used == Decimal("4.5")
    assert result.rate_source == "test-fixed"


# ---------------------------------------------------------------
# 3. 100 USD -> 720 RMB -> 3240 TWD
# ---------------------------------------------------------------
def test_usd_conversion():
    svc = make_service()
    m = Money(Decimal("100"), Currency.USD)
    result = svc.to_twd(m)

    # USD → RMB: 100 × 7.2 = 720；RMB → TWD: 720 × 4.5 = 3240
    assert result.twd_amount == Decimal("3240")
    assert result.rate_used == Decimal("32.4")  # 7.2 × 4.5 合併匯率


# ---------------------------------------------------------------
# 4. UNKNOWN currency → raise ValueError
# ---------------------------------------------------------------
def test_unknown_currency_raises():
    svc = make_service()
    m = Money(Decimal("100"), Currency.UNKNOWN)
    with pytest.raises(ValueError):
        svc.to_twd(m)


# ---------------------------------------------------------------
# 5. 負數 → raise ValueError
# ---------------------------------------------------------------
def test_negative_amount_raises():
    with pytest.raises(ValueError):
        Money(Decimal("-1"), Currency.TWD)


# ---------------------------------------------------------------
# 6. Money immutable（frozen=True）
# ---------------------------------------------------------------
def test_money_immutable():
    m = Money(Decimal("100"), Currency.TWD)
    with pytest.raises(FrozenInstanceError):
        m.amount = Decimal("200")  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        m.currency = Currency.RMB  # type: ignore[misc]

    # 原始值未被覆寫
    assert m.amount == Decimal("100")
    assert m.currency is Currency.TWD


# ---------------------------------------------------------------
# 7. ConvertedMoney 不可再次傳入 to_twd（防止 RMB→TWD→TWD）
# ---------------------------------------------------------------
def test_converted_money_rejected():
    svc = make_service()
    m = Money(Decimal("1000"), Currency.RMB)
    converted = svc.to_twd(m)

    with pytest.raises(TypeError):
        svc.to_twd(converted)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 8. Decimal 精度測試（避免 float 誤差）
# ---------------------------------------------------------------
def test_decimal_precision():
    svc = make_service()
    # 123.45 RMB × 4.5 = 555.525（float 會有誤差，Decimal 精確）
    m = Money(Decimal("123.45"), Currency.RMB)
    result = svc.to_twd(m)
    assert result.twd_amount == Decimal("555.525")

    # 0.1 × 3 = 0.3（float 是 0.30000000000000004）
    m2 = Money(Decimal("0.1"), Currency.USD)
    r2 = svc.to_twd(m2)
    assert r2.twd_amount == Decimal("3.24")  # 0.1 × 7.2 × 4.5


# ---------------------------------------------------------------
# 9. 拒絕 float（精度政策）
# ---------------------------------------------------------------
def test_float_rejected():
    with pytest.raises(TypeError):
        Money(100.5, Currency.TWD)  # type: ignore[arg-type]

    # float 匯率也拒絕
    with pytest.raises(TypeError):
        CurrencyService(rmb_to_twd=4.5, usd_to_rmb=7.2, rate_source="bad")  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 10. int / str 自動轉 Decimal（便利性）
# ---------------------------------------------------------------
def test_int_str_coerced():
    m1 = Money(1000, Currency.RMB)
    assert m1.amount == Decimal("1000")
    m2 = Money("1000", Currency.RMB)
    assert m2.amount == Decimal("1000")
    svc = make_service()
    assert svc.to_twd(m1).twd_amount == Decimal("4500")


# ---------------------------------------------------------------
# 11. ConvertedMoney 欄位完整性
# ---------------------------------------------------------------
def test_converted_money_fields():
    svc = make_service()
    m = Money(Decimal("1000"), Currency.RMB)
    result = svc.to_twd(m)
    assert result.original.amount == Decimal("1000")
    assert result.original.currency is Currency.RMB
    assert result.twd_amount == Decimal("4500")
    assert result.rate_used == Decimal("4.5")
    assert result.rate_source == "test-fixed"


# ---------------------------------------------------------------
# 12. NaN 金額 → ValueError
# ---------------------------------------------------------------
def test_nan_amount_raises():
    with pytest.raises(ValueError):
        Money("NaN", Currency.TWD)
    with pytest.raises(ValueError):
        Money(Decimal("NaN"), Currency.RMB)


# ---------------------------------------------------------------
# 13. 匯率為 0 → ValueError（0 匯率會產生 0 TWD，無意義）
# ---------------------------------------------------------------
def test_zero_rate_raises():
    with pytest.raises(ValueError):
        CurrencyService(rmb_to_twd=Decimal("0"), usd_to_rmb=Decimal("7.2"), rate_source="test")
    with pytest.raises(ValueError):
        CurrencyService(rmb_to_twd=Decimal("4.5"), usd_to_rmb=Decimal("0"), rate_source="test")
    # 負匯率同樣拒絕
    with pytest.raises(ValueError):
        CurrencyService(rmb_to_twd=Decimal("-4.5"), usd_to_rmb=Decimal("7.2"), rate_source="test")
