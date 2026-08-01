"""
vision_result.py — Vision 回傳標準化中介層（V2 Phase 6.3A）

VisionRawItem / VisionRawResult 是 Vision 回傳的標準化表示：
- 不得直接等同 ItemCandidate
- 不得自動相信 market_hash_name
- 不得執行貨幣換算
- price_type 不確定時必須 UNKNOWN
"""
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
import copy

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.image_evidence import ImageKind, ImagePlatform
from alkaid_cs2.domain.item_candidate import ItemRole
from alkaid_cs2.domain.price_candidate import PriceType


@dataclass
class VisionRawItem:
    raw_name: str | None = None
    market_hash_name: str | None = None
    weapon: str | None = None
    skin: str | None = None
    wear: str | None = None
    stattrak: bool | None = None
    price_amount: Decimal | None = None
    currency: Currency = Currency.UNKNOWN
    price_type: PriceType = PriceType.UNKNOWN
    role: ItemRole = ItemRole.UNKNOWN
    platform: ImagePlatform = ImagePlatform.UNKNOWN
    confidence: float = 0.0
    evidence_text: str = ""
    bbox: tuple[int, int, int, int] | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        import math

        # str|None 欄位
        for attr in ("raw_name", "market_hash_name", "weapon", "skin", "wear"):
            v = getattr(self, attr)
            if v is not None and not isinstance(v, str):
                raise TypeError(f"{attr} 必須是 str 或 None，收到 {type(v).__name__}")
        # stattrak：bool 或 None
        if self.stattrak is not None and not isinstance(self.stattrak, bool):
            raise TypeError(f"stattrak 必須是 bool 或 None，收到 {type(self.stattrak).__name__}")
        if not isinstance(self.currency, Currency):
            raise TypeError(f"currency 必須是 Currency enum，收到 {type(self.currency).__name__}")
        if not isinstance(self.price_type, PriceType):
            raise TypeError(f"price_type 必須是 PriceType enum，收到 {type(self.price_type).__name__}")
        if not isinstance(self.role, ItemRole):
            raise TypeError(f"role 必須是 ItemRole enum，收到 {type(self.role).__name__}")
        if not isinstance(self.platform, ImagePlatform):
            raise TypeError(f"platform 必須是 ImagePlatform enum，收到 {type(self.platform).__name__}")
        if isinstance(self.confidence, bool):
            raise TypeError("confidence 不接受 bool")
        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence 必須是 int 或 float")
        if not math.isfinite(self.confidence):
            raise ValueError(f"confidence 必須是有限數值，收到 {self.confidence}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必須介於 0.0-1.0，收到 {self.confidence}")
        if self.price_amount is not None:
            if not isinstance(self.price_amount, Decimal):
                raise TypeError(f"price_amount 必須是 Decimal 或 None，收到 {type(self.price_amount).__name__}")
            if not self.price_amount.is_finite():
                raise ValueError(f"price_amount 必須是有限 Decimal，收到 {self.price_amount}")
        if self.bbox is not None:
            if not isinstance(self.bbox, tuple) or len(self.bbox) != 4:
                raise TypeError(f"bbox 必須是 4 元組或 None，收到 {self.bbox!r}")
            if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in self.bbox):
                raise ValueError(f"bbox 必須為非負 int（拒 bool），收到 {self.bbox!r}")
            x1, y1, x2, y2 = self.bbox
            if x2 < x1 or y2 < y1:
                raise ValueError(f"bbox 順序無效（x2>=x1、y2>=y1），收到 {self.bbox!r}")
        if not isinstance(self.evidence_text, str):
            raise TypeError(f"evidence_text 必須是 str，收到 {type(self.evidence_text).__name__}")
        if not isinstance(self.warnings, list):
            raise TypeError("warnings 必須是 list")
        if any(not isinstance(w, str) or not w.strip() for w in self.warnings):
            raise ValueError("warnings 不得含空白字串")
        self.warnings = list(self.warnings)  # defensive copy


@dataclass
class VisionRawResult:
    image_index: int
    image_kind: ImageKind
    platform: ImagePlatform
    items: list[VisionRawItem] = field(default_factory=list)
    overall_confidence: float = 0.0
    raw_payload: dict[str, object] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        import math

        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError(f"image_index 必須是非負 int，收到 {type(self.image_index).__name__}")
        if self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        if not isinstance(self.image_kind, ImageKind):
            raise TypeError("image_kind 必須是 ImageKind enum")
        if not isinstance(self.platform, ImagePlatform):
            raise TypeError("platform 必須是 ImagePlatform enum")
        for it in self.items:
            if not isinstance(it, VisionRawItem):
                raise TypeError(f"items 每筆必須是 VisionRawItem，收到 {type(it).__name__}")
        if isinstance(self.overall_confidence, bool):
            raise TypeError("overall_confidence 不接受 bool")
        if not isinstance(self.overall_confidence, (int, float)):
            raise TypeError("overall_confidence 必須是 int 或 float")
        if not math.isfinite(self.overall_confidence):
            raise ValueError(f"overall_confidence 必須是有限數值，收到 {self.overall_confidence}")
        if not (0.0 <= self.overall_confidence <= 1.0):
            raise ValueError(f"overall_confidence 必須介於 0.0-1.0，收到 {self.overall_confidence}")
        if not isinstance(self.raw_payload, dict):
            raise TypeError("raw_payload 必須是 dict")
        for attr in ("warnings", "errors"):
            v = getattr(self, attr)
            if not isinstance(v, list):
                raise TypeError(f"{attr} 必須是 list")
            if any(not isinstance(w, str) or not w.strip() for w in v):
                raise ValueError(f"{attr} 不得含空白字串")
        self.items = list(self.items)
        self.raw_payload = copy.deepcopy(self.raw_payload)  # 深拷貝
        self.warnings = list(self.warnings)
        self.errors = list(self.errors)
