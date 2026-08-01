"""
ItemCandidate — 商品候選領域模型（V2 Phase 3）

解決「字典第一命中立即 return、同文多商品只能保留一件」的問題：
- 一篇貼文可產出多個 ItemCandidate
- 每個候選保存 matched_key、位置、證據來源、角色
- 不自動驗證、不自動查價、不自動換算
"""
from dataclasses import dataclass, field
from enum import Enum


class ItemRole(str, Enum):
    """商品在貼文中的角色。"""
    SELLING = "selling"
    BUYING = "buying"
    REFERENCE = "reference"
    TRADE = "trade"
    BUNDLE = "bundle"
    PRICE_CHECK = "price_check"
    SHOWCASE = "showcase"
    INVENTORY = "inventory"
    DISCUSSION = "discussion"
    UNKNOWN = "unknown"


class ItemEvidence(str, Enum):
    """候選的證據來源。"""
    DICT_FULL = "dict_full"        # 完整名稱字典命中
    DICT_PATTERN = "dict_pattern"  # 花紋字典命中
    VISION = "vision"              # 圖片辨識
    FLASH = "flash"                # Flash 模型抽取
    PRO = "pro"                    # Pro 模型消歧
    LEGACY = "legacy"              # 舊流程
    UNKNOWN = "unknown"


@dataclass
class ItemCandidate:
    market_hash_name: str | None = None
    weapon: str | None = None
    skin: str | None = None
    wear: str | None = None
    stattrak: bool = False
    role: ItemRole = ItemRole.UNKNOWN
    original_text: str = ""
    matched_key: str | None = None
    match_start: int | None = None
    match_end: int | None = None
    matched_text: str | None = None
    parser: str = ""
    evidence: ItemEvidence = ItemEvidence.UNKNOWN
    confidence: float = 0.0
    score: float = 0.0
    verified: bool = False
    verified_by: str | None = None
    validation_error: str | None = None
    image_index: int | None = None
    linked_price_indexes: list[int] = field(default_factory=list)

    def __post_init__(self) -> None:
        import math

        if not isinstance(self.role, ItemRole):
            raise TypeError(f"role 必須是 ItemRole enum，收到 {type(self.role).__name__}")
        if not isinstance(self.evidence, ItemEvidence):
            raise TypeError(f"evidence 必須是 ItemEvidence enum，收到 {type(self.evidence).__name__}")
        if not self.original_text or not self.original_text.strip():
            raise ValueError("original_text 不可為空白")
        if not self.parser or not self.parser.strip():
            raise ValueError("parser 不可為空白")
        # confidence
        if isinstance(self.confidence, bool):
            raise TypeError("confidence 不接受 bool")
        if not isinstance(self.confidence, (int, float)):
            raise TypeError("confidence 必須是 int 或 float")
        if not math.isfinite(self.confidence):
            raise ValueError(f"confidence 必須是有限數值，收到 {self.confidence}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 必須介於 0.0-1.0，收到 {self.confidence}")
        # score
        if isinstance(self.score, bool):
            raise TypeError("score 不接受 bool")
        if not isinstance(self.score, (int, float)):
            raise TypeError("score 必須是 int 或 float")
        if not math.isfinite(self.score):
            raise ValueError(f"score 必須是有限數值，收到 {self.score}")
        # 位置
        if self.match_start is not None and self.match_start < 0:
            raise ValueError(f"match_start 不可為負數，收到 {self.match_start}")
        if self.match_end is not None and self.match_end < 0:
            raise ValueError(f"match_end 不可為負數，收到 {self.match_end}")
        if (self.match_start is not None and self.match_end is not None
                and self.match_end < self.match_start):
            raise ValueError(
                f"match_end({self.match_end}) 必須 >= match_start({self.match_start})"
            )
        # matched_text：有位置時必須等於原文切片（None 則自動填入）
        if self.match_start is not None and self.match_end is not None:
            if self.match_end > len(self.original_text):
                raise ValueError(
                    f"match_end({self.match_end}) 超過 original_text 長度({len(self.original_text)})"
                )
            expected = self.original_text[self.match_start:self.match_end]
            if self.matched_text is None:
                object.__setattr__(self, "matched_text", expected)
            elif self.matched_text != expected:
                raise ValueError(
                    f"matched_text({self.matched_text!r}) 與原文切片({expected!r})不一致"
                )
        # 無位置時 matched_text 允許 None（或由呼叫端自由設定）
        if self.image_index is not None and self.image_index < 0:
            raise ValueError(f"image_index 不可為負數，收到 {self.image_index}")
        # linked_price_indexes
        if any(i < 0 for i in self.linked_price_indexes):
            raise ValueError("linked_price_indexes 不得含負數")
        if len(set(self.linked_price_indexes)) != len(self.linked_price_indexes):
            raise ValueError("linked_price_indexes 不得有重複值")
        # verified=True 時名稱不可為空
        if self.verified and not self.market_hash_name:
            raise ValueError("verified=True 時 market_hash_name 不可為空")
        # verified=False + validation_error 允許（unresolved 候選保留診斷）
