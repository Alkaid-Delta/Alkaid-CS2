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
    - __post_init__ 強制契約：rate_used>0、rate_source 合法
    """

    original: Money
    twd_amount: Decimal
    rate_used: Decimal
    rate_source: str

    _FORBIDDEN_RATE_SOURCES = frozenset(
        {"unknown", "llm", "model", "vision", "ocr"})

    def __post_init__(self) -> None:
        # P1.3：exact Money type（Money subclass 拒絕——避免覆寫
        # property/__getattribute__ 在金額邊界擴張型別）
        if type(self.original) is not Money:
            raise TypeError(
                "ConvertedMoney.original 必須是精確 Money（不接受 subclass）")
        # twd_amount 強制 Decimal（float/bool 拒絕）
        twd = _coerce_decimal(self.twd_amount)
        if twd.is_nan() or twd.is_infinite():
            raise ValueError("twd_amount 不得為 NaN/Infinity")
        if twd < 0:
            raise ValueError("twd_amount 不得為負數")
        object.__setattr__(self, "twd_amount", twd)
        rate = _coerce_decimal(self.rate_used)
        if rate.is_nan() or rate.is_infinite():
            raise ValueError("rate_used 不得為 NaN/Infinity")
        if rate <= 0:
            raise ValueError("rate_used 必須 > 0")
        object.__setattr__(self, "rate_used", rate)
        if not isinstance(self.rate_source, str) or not self.rate_source.strip():
            raise ValueError("rate_source 必須為非空字串")
        if len(self.rate_source) > 128:
            raise ValueError("rate_source 長度不得超過 128")
        if not all(c.isalnum() or c in "-_." for c in self.rate_source):
            raise ValueError("rate_source 含不合法字元")
        if self.rate_source.strip().lower() in self._FORBIDDEN_RATE_SOURCES:
            raise ValueError(
                f"rate_source 不得為 {self.rate_source!r}（llm/model/vision/"
                "ocr/unknown 禁止）")
