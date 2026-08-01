"""
PriceCandidate — 價格領域模型（V2 Phase 2）

可區分 seller ask / BUFF floor / reference / calculated / bundle total 的價格。
保存原始 Money（不可變），converted 由外部 CurrencyService 填寫，本模型不自動換算。
"""
from dataclasses import dataclass
from enum import Enum

from alkaid_cs2.domain.price import ConvertedMoney, Money


class PriceType(str, Enum):
    """價格角色。"""
    SELLER_ASK = "seller_ask"      # 賣家開價
    REFERENCE = "reference"        # 參考價（無明確交易語意）
    BUFF_FLOOR = "buff_floor"      # BUFF 同磨損最低價
    CALCULATED = "calculated"      # 由算式產出（如 2100*4.4=9200 的 9200）
    BUNDLE_TOTAL = "bundle_total"  # 整包/多件合計
    UNKNOWN = "unknown"


class PriceSource(str, Enum):
    """價格來源媒介。"""
    TEXT = "text"                      # 貼文文字
    IMAGE = "image"                    # 圖片
    CHAT = "chat"                      # 聊天截圖
    MARKET_SCREENSHOT = "market_screenshot"  # 市集/BUFF 截圖
    CALCULATION = "calculation"        # 算式推導


@dataclass
class PriceCandidate:
    money: Money
    price_type: PriceType
    source: PriceSource
    evidence: str
    confidence: float
    text_start: int | None = None
    text_end: int | None = None
    image_index: int | None = None
    associated_item_index: int | None = None
    converted: ConvertedMoney | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.money, Money):
            raise TypeError(f"money 必須是 Money，收到 {type(self.money).__name__}")
        if not isinstance(self.price_type, PriceType):
            raise TypeError("price_type 必須是 PriceType enum")
        if not isinstance(self.source, PriceSource):
            raise TypeError("source 必須是 PriceSource enum")
        if not self.evidence or not self.evidence.strip():
            raise ValueError("evidence 不可為空白")
        # converted：None 或 ConvertedMoney
        if self.converted is not None and not isinstance(self.converted, ConvertedMoney):
            raise TypeError(
                f"converted 必須是 ConvertedMoney 或 None，收到 {type(self.converted).__name__}"
            )
        # confidence：不接受 bool、必須 int/float、必須 finite
        if isinstance(self.confidence, bool):
            raise TypeError("confidence 不接受 bool")
        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence 必須是 int 或 float")
        import math
        if not math.isfinite(self.confidence):
            raise ValueError(f"confidence 必須是有限數值，收到 {self.confidence}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必須介於 0.0-1.0，收到 {self.confidence}")
        if self.text_start is not None and self.text_start < 0:
            raise ValueError(f"text_start 不可為負數，收到 {self.text_start}")
        if self.text_end is not None and self.text_end < 0:
            raise ValueError(f"text_end 不可為負數，收到 {self.text_end}")
        if (self.text_start is not None and self.text_end is not None
                and self.text_end < self.text_start):
            raise ValueError(
                f"text_end({self.text_end}) 必須 >= text_start({self.text_start})"
            )
        if self.image_index is not None and self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        if self.associated_item_index is not None and self.associated_item_index < 0:
            raise ValueError(
                f"associated_item_index 不可為負數，收到 {self.associated_item_index}"
            )
        # 不得自動換算：converted 只能由外部 CurrencyService 填寫（此處保持 None）
        # 不得修改原始 Money：Money 為 frozen dataclass，不可變
