"""
evaluation/models.py — Vision 評估資料模型（Phase 6.4A）

EvaluationCase / ExpectedItem / EvaluationImage 及驗證。
Decimal 一律以字串在 JSON 保存。
"""
import copy
import re
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum

from alkaid_cs2.domain.enums import Currency


class EvaluationSource(str, Enum):
    SYNTHETIC = "synthetic"
    ANONYMIZED_REAL = "anonymized_real"
    MANUAL_FIXTURE = "manual_fixture"


class ExpectedImageKind(str, Enum):
    INVENTORY = "inventory"
    SINGLE = "single"
    MULTI = "multi"
    MARKET = "market"
    CHAT = "chat"
    INSPECT = "inspect"
    PAYMENT = "payment"
    TRADE = "trade"
    OTHER = "other"
    UNKNOWN = "unknown"


_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _require_str_not_blank(value, name: str) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValueError(f"{name} 若非 None 必須是非空白 str")


def _validate_index_list(value, name: str) -> list[int]:
    if not isinstance(value, list):
        raise TypeError(f"{name} 必須是 list[int]")
    out: list[int] = []
    for x in value:
        if isinstance(x, bool) or not isinstance(x, int):
            raise TypeError(f"{name} 每項必須是非負 int，收到 {type(x).__name__}")
        if x < 0:
            raise ValueError(f"{name} 不可含負數：{x}")
        out.append(x)
    if len(out) != len(set(out)):
        raise ValueError(f"{name} 不得重複：{out}")
    return out


@dataclass
class ExpectedItem:
    market_hash_name: str | None = None
    weapon: str | None = None
    skin: str | None = None
    wear: str | None = None
    stattrak: bool | None = None
    role: str | None = None
    seller_price: Decimal | None = None
    currency: Currency | None = None
    image_indexes: list[int] = field(default_factory=list)
    notes: str | None = None

    def __post_init__(self) -> None:
        for name in ("market_hash_name", "weapon", "skin", "wear", "role", "notes"):
            _require_str_not_blank(getattr(self, name), name)
        if self.stattrak is not None and not isinstance(self.stattrak, bool):
            raise TypeError(f"stattrak 必須 bool 或 None，收到 {type(self.stattrak).__name__}")
        if self.seller_price is not None:
            if not isinstance(self.seller_price, Decimal):
                raise TypeError(f"seller_price 必須 Decimal 或 None，收到 {type(self.seller_price).__name__}")
            if not self.seller_price.is_finite():
                raise ValueError(f"seller_price 必須 finite，收到 {self.seller_price}")
            if self.seller_price <= 0:
                raise ValueError(f"seller_price 必須 > 0，收到 {self.seller_price}")
        if self.currency is not None and not isinstance(self.currency, Currency):
            raise TypeError(f"currency 必須 Currency 或 None，收到 {type(self.currency).__name__}")
        self.image_indexes = _validate_index_list(self.image_indexes, "image_indexes")
        # defensive copy（Decimal 不可變無需複製）
        self.notes = self.notes.strip() if self.notes else None
        for name in ("market_hash_name", "weapon", "skin", "wear", "role"):
            v = getattr(self, name)
            if v is not None:
                setattr(self, name, v.strip())


@dataclass
class EvaluationImage:
    image_index: int
    image_url: str
    image_kind: ExpectedImageKind
    vision_payload: object | None = None
    expected_item_indexes: list[int] = field(default_factory=list)
    should_create_price: bool = False
    notes: str | None = None

    def __post_init__(self) -> None:
        if isinstance(self.image_index, bool) or not isinstance(self.image_index, int):
            raise TypeError(f"image_index 必須是非負 int，收到 {type(self.image_index).__name__}")
        if self.image_index < 0:
            raise ValueError(f"image_index 不可為負數：{self.image_index}")
        if not isinstance(self.image_url, str) or not self.image_url.strip():
            raise ValueError("image_url 必須是非空 str")
        if not isinstance(self.image_kind, ExpectedImageKind):
            raise TypeError(f"image_kind 必須 ExpectedImageKind，收到 {type(self.image_kind).__name__}")
        if not isinstance(self.should_create_price, bool):
            raise TypeError(f"should_create_price 必須 bool，收到 {type(self.should_create_price).__name__}")
        self.expected_item_indexes = _validate_index_list(
            self.expected_item_indexes, "expected_item_indexes")
        self.image_url = self.image_url.strip()
        self.notes = self.notes.strip() if self.notes else None
        # defensive copy
        self.vision_payload = copy.deepcopy(self.vision_payload)


@dataclass
class EvaluationCase:
    case_id: str
    source: EvaluationSource
    author: str
    link: str
    raw_text: str
    images: list[EvaluationImage] = field(default_factory=list)
    expected_items: list[ExpectedItem] = field(default_factory=list)
    expected_post_intent: str = ""
    expected_safe_for_production: bool = False
    expected_raw_vision_safe: bool | None = None
    tags: list[str] = field(default_factory=list)
    notes: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id 必須是非空 str")
        if not _CASE_ID_RE.match(self.case_id):
            raise ValueError(
                f"case_id 只能含 a-z A-Z 0-9 _ -：{self.case_id!r}")
        if not isinstance(self.source, EvaluationSource):
            raise TypeError(f"source 必須 EvaluationSource，收到 {type(self.source).__name__}")
        for name in ("author", "link", "raw_text"):
            if not isinstance(getattr(self, name), str):
                raise TypeError(f"{name} 必須 str，收到 {type(getattr(self, name)).__name__}")
        if not isinstance(self.expected_safe_for_production, bool):
            raise TypeError("expected_safe_for_production 必須 bool")
        if self.expected_raw_vision_safe is not None and \
                not isinstance(self.expected_raw_vision_safe, bool):
            raise TypeError("expected_raw_vision_safe 必須 bool 或 None")
        if not all(isinstance(im, EvaluationImage) for im in self.images):
            raise TypeError("images 每筆必須 EvaluationImage")
        if not all(isinstance(it, ExpectedItem) for it in self.expected_items):
            raise TypeError("expected_items 每筆必須 ExpectedItem")

        # image_index 不得重複
        seen_idx: set[int] = set()
        for im in self.images:
            if im.image_index in seen_idx:
                raise ValueError(f"case {self.case_id} 的 image_index 重複：{im.image_index}")
            seen_idx.add(im.image_index)
        # expected_item_indexes 不得超出 expected_items 範圍
        for im in self.images:
            for idx in im.expected_item_indexes:
                if idx >= len(self.expected_items):
                    raise ValueError(
                        f"case {self.case_id} 的 expected_item_indexes 超出範圍：{idx}")

        # tags 非空字串、去重保序
        tags: list[str] = []
        for t in self.tags:
            if not isinstance(t, str) or not t.strip():
                raise ValueError(f"case {self.case_id} 的 tags 含空白項")
            t = t.strip()
            if t not in tags:
                tags.append(t)
        self.tags = tags
        self.notes = self.notes.strip() if self.notes else None
        self.author = self.author.strip()
        self.link = self.link.strip()
        self.raw_text = self.raw_text.strip()
        # defensive copy
        self.images = copy.deepcopy(self.images)
        self.expected_items = copy.deepcopy(self.expected_items)


def parse_decimal(value: object, name: str) -> Decimal | None:
    """JSON 字串 → Decimal。

    契約：只接受 str 或 None（JSON 的 Decimal 一律字串保存）；
    拒絕 int/float/Decimal/bool（避免靜默轉型）。
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"{name} 只接受字串或 None，收到 {type(value).__name__}")
    d = Decimal(value)
    if not d.is_finite():
        raise ValueError(f"{name} 必須 finite：{value}")
    return d


def parse_currency(value: object) -> Currency | None:
    if value is None:
        return None
    if isinstance(value, Currency):
        return value
    if not isinstance(value, str):
        raise TypeError(f"currency 必須字串，收到 {type(value).__name__}")
    try:
        return Currency(value.upper())
    except ValueError:
        raise ValueError(f"未知 currency：{value!r}") from None
