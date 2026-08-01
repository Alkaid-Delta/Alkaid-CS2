"""
evidence_merge.py — 文字與圖片證據合併模型（V2 Phase 6.3B）

MergeSource / ConflictType / EvidenceConflict / MergedEvidenceResult。
純資料容器：不呼叫任何服務。
"""
from dataclasses import dataclass, field
from enum import Enum

from alkaid_cs2.domain.image_evidence import ImageEvidence
from alkaid_cs2.domain.parsed_post import ParsedPost

VALID_SEVERITIES = ("info", "warning", "error")


class MergeSource(str, Enum):
    TEXT = "text"
    IMAGE = "image"
    TEXT_AND_IMAGE = "text_and_image"


class ConflictType(str, Enum):
    ITEM_NAME_CONFLICT = "item_name_conflict"
    PRICE_CONFLICT = "price_conflict"
    CURRENCY_CONFLICT = "currency_conflict"
    ROLE_CONFLICT = "role_conflict"
    WEAR_CONFLICT = "wear_conflict"
    DUPLICATE_EVIDENCE = "duplicate_evidence"
    AMBIGUOUS_LINK = "ambiguous_link"
    UNKNOWN = "unknown"


@dataclass
class EvidenceConflict:
    conflict_type: ConflictType
    reason: str
    severity: str
    text_item_index: int | None = None
    image_item_index: int | None = None
    text_price_index: int | None = None
    image_price_index: int | None = None
    image_index: int | None = None
    resolved: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.conflict_type, ConflictType):
            raise TypeError(f"conflict_type 必須是 ConflictType enum，收到 {type(self.conflict_type).__name__}")
        if not self.reason or not self.reason.strip():
            raise ValueError("reason 不可空白")
        if self.severity not in VALID_SEVERITIES:
            raise ValueError(f"severity 必須是 {VALID_SEVERITIES}，收到 {self.severity!r}")
        if not isinstance(self.resolved, bool):
            raise TypeError(f"resolved 必須是 bool，收到 {type(self.resolved).__name__}")
        for attr in ("text_item_index", "image_item_index", "text_price_index",
                     "image_price_index", "image_index"):
            v = getattr(self, attr)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{attr} 必須是非負 int 或 None，收到 {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{attr} 不可為負數，收到 {v}")


@dataclass
class MergedEvidenceResult:
    parsed_post: ParsedPost
    image_evidence: list[ImageEvidence] = field(default_factory=list)
    conflicts: list[EvidenceConflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    text_item_count: int = 0
    image_item_count: int = 0
    merged_item_count: int = 0
    text_price_count: int = 0
    image_price_count: int = 0
    merged_price_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.parsed_post, ParsedPost):
            raise TypeError(f"parsed_post 必須是 ParsedPost，收到 {type(self.parsed_post).__name__}")
        for ev in self.image_evidence:
            if not isinstance(ev, ImageEvidence):
                raise TypeError(f"image_evidence 每筆必須是 ImageEvidence，收到 {type(ev).__name__}")
        for c in self.conflicts:
            if not isinstance(c, EvidenceConflict):
                raise TypeError(f"conflicts 每筆必須是 EvidenceConflict，收到 {type(c).__name__}")
        if not isinstance(self.warnings, list):
            raise TypeError("warnings 必須是 list")
        if any(not isinstance(w, str) or not w.strip() for w in self.warnings):
            raise ValueError("warnings 不得含空白字串")
        for attr in ("text_item_count", "image_item_count", "merged_item_count",
                     "text_price_count", "image_price_count", "merged_price_count"):
            v = getattr(self, attr)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{attr} 必須是非負 int，收到 {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{attr} 不可為負數，收到 {v}")
        # defensive copy
        self.image_evidence = list(self.image_evidence)
        self.conflicts = list(self.conflicts)
        self.warnings = list(self.warnings)
