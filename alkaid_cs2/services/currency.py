"""
CurrencyService — 全專案唯一的匯率換算服務。

規則（Blueprint §8 Currency Policy）：
- 只有 CurrencyService 能換算金額
- 全部使用 Decimal，禁止 float
- TWD -> TWD rate=1（不換算）
- RMB -> TWD（× rmb_to_twd）
- USD -> RMB -> TWD（× usd_to_rmb × rmb_to_twd）
- UNKNOWN -> raise ValueError
- 輸入只接受 Money（ConvertedMoney 不是 Money → 拒絕，防止重複換算）
"""
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.price import ConvertedMoney, Money


def _coerce_rate(value) -> Decimal:
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        raise TypeError("匯率不接受 float，請用 str/int（Decimal 精度）")
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        return Decimal(value)
    raise TypeError(f"匯率不支援的型別: {type(value).__name__}")


class CurrencyService:
    def __init__(
        self,
        *,
        rmb_to_twd: Decimal,
        usd_to_rmb: Decimal,
        rate_source: str,
    ) -> None:
        self.rmb_to_twd = _coerce_rate(rmb_to_twd)
        self.usd_to_rmb = _coerce_rate(usd_to_rmb)
        if self.rmb_to_twd <= 0:
            raise ValueError("rmb_to_twd 必須為正數")
        if self.usd_to_rmb <= 0:
            raise ValueError("usd_to_rmb 必須為正數")
        self.rate_source = rate_source

    def to_twd(self, money: Money) -> ConvertedMoney:
        """換算 Money 為 TWD，回傳 ConvertedMoney。輸入只接受 Money。"""
        if not isinstance(money, Money):
            raise TypeError(
                f"to_twd 只接受 Money，收到 {type(money).__name__}"
                "（ConvertedMoney 不可再次換算）"
            )

        if money.currency is Currency.TWD:
            rate_used = Decimal("1")
            twd_amount = money.amount
        elif money.currency is Currency.RMB:
            rate_used = self.rmb_to_twd
            twd_amount = money.amount * rate_used
        elif money.currency is Currency.USD:
            rate_used = self.usd_to_rmb * self.rmb_to_twd
            twd_amount = money.amount * rate_used
        else:  # UNKNOWN
            raise ValueError(f"Unsupported currency: {money.currency}")

        return ConvertedMoney(
            original=money,
            twd_amount=twd_amount,
            rate_used=rate_used,
            rate_source=self.rate_source,
        )
