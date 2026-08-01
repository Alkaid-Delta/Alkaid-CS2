"""
image_evidence.py — 圖片證據領域模型（V2 Phase 6.3A）

把 Vision / OCR 回傳轉為標準化證據，可安全合併進 ParsedPost。
- 純資料容器：不得自動呼叫 Vision
- mutable 欄位 defensive copy
"""
from dataclasses import dataclass, field
from enum import Enum
import copy

from alkaid_cs2.domain.item_candidate import ItemCandidate
from alkaid_cs2.domain.price_candidate import PriceCandidate


class ImageKind(str, Enum):
    SINGLE_ITEM = "single_item"
    MULTI_ITEM = "multi_item"
    INVENTORY_GRID = "inventory_grid"
    MARKET_LISTING = "market_listing"
    CHAT_SCREENSHOT = "chat_screenshot"
    INSPECT_SCREENSHOT = "inspect_screenshot"
    PAYMENT_PROOF = "payment_proof"
    TRADE_CONFIRMATION = "trade_confirmation"
    UNKNOWN = "unknown"


class ImagePlatform(str, Enum):
    STEAM = "steam"
    BUFF163 = "buff163"
    FACEBOOK = "facebook"
    UNKNOWN = "unknown"


class ImageEvidenceSource(str, Enum):
    VISION = "vision"
    OCR = "ocr"
    HYBRID = "hybrid"


@dataclass
class ImageEvidence:
    image_index: int
    image_url: str
    image_hash: str | None
    image_kind: ImageKind
    platform: ImagePlatform
    source: ImageEvidenceSource
    raw_result: dict[str, object]
    item_candidates: list[ItemCandidate] = field(default_factory=list)
    price_candidates: list[PriceCandidate] = field(default_factory=list)
    confidence: float = 0.0
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        import math

        # image_index：非負 int、拒絕 bool
        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError(f"image_index 必須是非負 int，收到 {type(self.image_index).__name__}")
        if self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        # image_url：非空 str
        if not isinstance(self.image_url, str) or not self.image_url.strip():
            raise ValueError("image_url 必須是非空 str")
        # image_hash：None 或非空 str
        if self.image_hash is not None and (not isinstance(self.image_hash, str) or not self.image_hash.strip()):
            raise ValueError("image_hash 若非 None 必須是非空 str")
        # enum 嚴格驗證
        if not isinstance(self.image_kind, ImageKind):
            raise TypeError(f"image_kind 必須是 ImageKind enum，收到 {type(self.image_kind).__name__}")
        if not isinstance(self.platform, ImagePlatform):
            raise TypeError(f"platform 必須是 ImagePlatform enum，收到 {type(self.platform).__name__}")
        if not isinstance(self.source, ImageEvidenceSource):
            raise TypeError(f"source 必須是 ImageEvidenceSource enum，收到 {type(self.source).__name__}")
        # raw_result：dict
        if not isinstance(self.raw_result, dict):
            raise TypeError(f"raw_result 必須是 dict，收到 {type(self.raw_result).__name__}")
        # items / prices 型別
        for it in self.item_candidates:
            if not isinstance(it, ItemCandidate):
                raise TypeError(f"item_candidates 每筆必須是 ItemCandidate，收到 {type(it).__name__}")
        for p in self.price_candidates:
            if not isinstance(p, PriceCandidate):
                raise TypeError(f"price_candidates 每筆必須是 PriceCandidate，收到 {type(p).__name__}")
        # confidence：有限、0-1、拒 bool
        if isinstance(self.confidence, bool):
            raise TypeError("confidence 不接受 bool")
        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence 必須是 int 或 float")
        if not math.isfinite(self.confidence):
            raise ValueError(f"confidence 必須是有限數值，收到 {self.confidence}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必須介於 0.0-1.0，收到 {self.confidence}")
        # warnings / errors：list[str] 非空白
        for attr in ("warnings", "errors"):
            v = getattr(self, attr)
            if not isinstance(v, list):
                raise TypeError(f"{attr} 必須是 list")
            if any(not isinstance(w, str) or not w.strip() for w in v):
                raise ValueError(f"{attr} 不得含空白字串")
        # defensive copy（深拷貝：巢狀結構也不受外部修改影響）
        self.raw_result = copy.deepcopy(self.raw_result)
        self.item_candidates = list(self.item_candidates)
        self.price_candidates = list(self.price_candidates)
        self.warnings = list(self.warnings)
        self.errors = list(self.errors)
