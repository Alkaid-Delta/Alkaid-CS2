"""領域物件（V2 domain price model）。"""
from dataclasses import dataclass
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency


def _coerce_decimal(value) -> Decimal:
    """把 int / str / Decimal 轉成 Decimal。拒絕 float（避免精度損失）。"""
    if isinstance(value, Decimal):
        return value
    if isinstance(value, bool):
        raise TypeError("Money.amount 不接受 bool")
    if isinstance(value, float):
        raise TypeError("Money.amount 不接受 float，請用 str 或 int（Decimal 精度）")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"Money.amount 不支援的型別: {type(value).__name__}")


@dataclass(frozen=True)
class Money:
    """
    不可變金額：保存原始 amount 與 currency，禁止負數。

    - frozen=True → 不可覆寫原始值
    - amount 一律 Decimal（拒絕 float）
    - 換算後的結果是獨立物件 ConvertedMoney，不污染原始 Money
    """

    amount: Decimal
    currency: Currency

    def __post_init__(self) -> None:
        # 強制 Decimal（int/str 可，float/bool 拒絕）
        object.__setattr__(self, "amount", _coerce_decimal(self.amount))
        if self.amount.is_nan():
            raise ValueError("Money.amount cannot be NaN")
        if self.amount.is_infinite():
            raise ValueError("Money.amount cannot be infinite")
        if self.amount < 0:
            raise ValueError("Money.amount cannot be negative")
        if not isinstance(self.currency, Currency):
            raise TypeError(f"Money.currency 必須是 Currency enum，收到 {type(self.currency).__name__}")


@dataclass(frozen=True)
class ConvertedMoney:
    """
    換算結果：保存原始 Money、換算後 TWD、使用匯率與來源。

    - original 不可變 → 原始幣別與金額永遠可追溯
    - twd_amount 只由 CurrencyService 產生一次
    - 不可作為 CurrencyService.to_twd 的輸入（型別不是 Money）
    """

    original: Money
    twd_amount: Decimal
    rate_used: Decimal
    rate_source: str
