"""
parsed_post.py — ParsedPost 領域模型（V2 Phase 5）

把 Raw Post、ItemCandidate、PriceCandidate、LinkDecision 統整為單一輸出。
- 純資料容器：不得自動呼叫任何 parser / service
- 全部欄位可驗證，禁止 mutable default
"""
from dataclasses import dataclass, field
from enum import Enum

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemRole
from alkaid_cs2.domain.price_candidate import PriceCandidate
from alkaid_cs2.services.price_item_linker import LinkDecision


class ParseStatus(str, Enum):
    OK = "ok"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass
class ParsedPost:
    post_id: str
    author: str = ""
    link: str = ""
    raw_text: str = ""
    image_urls: list[str] = field(default_factory=list)
    items: list[ItemCandidate] = field(default_factory=list)
    prices: list[PriceCandidate] = field(default_factory=list)
    link_decisions: list[LinkDecision] = field(default_factory=list)
    parse_status: ParseStatus = ParseStatus.UNRESOLVED
    intent: ItemRole = ItemRole.UNKNOWN
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    unlinked_item_indexes: list[int] = field(default_factory=list)
    unlinked_price_indexes: list[int] = field(default_factory=list)
    model_used: str | None = None
    escalation_reason: str | None = None
    source: str = ""
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.post_id, str) or not self.post_id.strip():
            raise ValueError("post_id 不可空白")
        for attr in ("author", "link"):
            v = getattr(self, attr)
            if not isinstance(v, str):
                raise TypeError(f"{attr} 必須是 str，收到 {type(v).__name__}")
        if not isinstance(self.raw_text, str):
            raise TypeError(f"raw_text 必須是 str，收到 {type(self.raw_text).__name__}")
        if self.image_urls is None or not isinstance(self.image_urls, list):
            raise TypeError("image_urls 必須是 list[str]（不接受 None）")
        if any(not isinstance(u, str) or not u.strip() for u in self.image_urls):
            raise ValueError("image_urls 不得含空白字串")
        # items / prices / link_decisions 型別
        if not isinstance(self.items, list):
            raise TypeError("items 必須是 list")
        for it in self.items:
            if not isinstance(it, ItemCandidate):
                raise TypeError(f"items 每筆必須是 ItemCandidate，收到 {type(it).__name__}")
        if not isinstance(self.prices, list):
            raise TypeError("prices 必須是 list")
        for p in self.prices:
            if not isinstance(p, PriceCandidate):
                raise TypeError(f"prices 每筆必須是 PriceCandidate，收到 {type(p).__name__}")
        if not isinstance(self.link_decisions, list):
            raise TypeError("link_decisions 必須是 list")
        for d in self.link_decisions:
            if not isinstance(d, LinkDecision):
                raise TypeError(f"link_decisions 每筆必須是 LinkDecision，收到 {type(d).__name__}")
        if not isinstance(self.parse_status, ParseStatus):
            raise TypeError(f"parse_status 必須是 ParseStatus enum，收到 {type(self.parse_status).__name__}")
        if not isinstance(self.intent, ItemRole):
            raise TypeError(f"intent 必須是 ItemRole enum，收到 {type(self.intent).__name__}")
        # warnings / errors：list[str]、不得含空白
        for attr in ("warnings", "errors"):
            v = getattr(self, attr)
            if not isinstance(v, list):
                raise TypeError(f"{attr} 必須是 list")
            if any(not isinstance(w, str) or not w.strip() for w in v):
                raise ValueError(f"{attr} 不得含空白字串")
        # unlinked indexes：非負、不重複、不超出對應清單長度
        self._validate_indexes(self.unlinked_item_indexes, len(self.items), "unlinked_item_indexes")
        self._validate_indexes(self.unlinked_price_indexes, len(self.prices), "unlinked_price_indexes")
        # model_used / escalation_reason：非 None 時不可空白
        for attr in ("model_used", "escalation_reason"):
            v = getattr(self, attr)
            if v is not None and (not isinstance(v, str) or not v.strip()):
                raise ValueError(f"{attr} 若非 None 不可空白")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source 不可空白")
        if self.metadata is None or not isinstance(self.metadata, dict):
            raise TypeError("metadata 必須是 dict（不接受 None）")

        # ── 全部驗證通過後，複製 mutable 欄位：避免呼叫端後續修改影響本物件 ──
        self.image_urls = list(self.image_urls)
        self.items = list(self.items)
        self.prices = list(self.prices)
        self.link_decisions = list(self.link_decisions)
        self.warnings = list(self.warnings)
        self.errors = list(self.errors)
        self.unlinked_item_indexes = list(self.unlinked_item_indexes)
        self.unlinked_price_indexes = list(self.unlinked_price_indexes)
        self.metadata = dict(self.metadata)

    @staticmethod
    def _validate_indexes(indexes: list[int], upper: int, name: str) -> None:
        if not isinstance(indexes, list):
            raise TypeError(f"{name} 必須是 list")
        for i in indexes:
            if isinstance(i, bool) or not isinstance(i, int):
                raise TypeError(f"{name} 每筆必須是 int，收到 {type(i).__name__}")
            if i < 0:
                raise ValueError(f"{name} 不得為負數，收到 {i}")
            if i >= upper:
                raise ValueError(f"{name} 不得超出對應清單長度({upper})，收到 {i}")
        if len(set(indexes)) != len(indexes):
            raise ValueError(f"{name} 不得有重複值")
