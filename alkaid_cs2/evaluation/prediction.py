"""
prediction.py — EvaluationPrediction 模型（Phase 6.4A）
"""
import copy
from dataclasses import dataclass, field
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency

VALID_PARSER_NAMES = ("legacy", "text_v2", "vision_v2", "vision_raw",
                      "vision_production")


@dataclass
class EvaluationPrediction:
    case_id: str
    parser_name: str
    source: str = ""
    blocked: bool = False
    parse_status: str = "parsed"
    market_hash_names: list[str] = field(default_factory=list)
    seller_prices: list[Decimal] = field(default_factory=list)
    seller_price_item_indexes: list[int] = field(default_factory=list)
    currencies: list[Currency | None] = field(default_factory=list)
    item_roles: list[str] = field(default_factory=list)
    wear_values: list[str] = field(default_factory=list)
    stattrak_values: list[bool | None] = field(default_factory=list)
    price_types: list[str] = field(default_factory=list)
    price_indexes: list[int] = field(default_factory=list)
    item_to_price_pairs: list[tuple[int, int]] = field(default_factory=list)
    linked_pairs: list[tuple[int, int]] = field(default_factory=list)
    conflicts: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    fallback_used: str | None = None
    latency_ms: float = 0.0
    image_count: int = 0
    vision_evidence_count: int = 0
    retry_count: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.case_id, str) or not self.case_id.strip():
            raise ValueError("case_id 必須是非空 str")
        if self.parser_name not in VALID_PARSER_NAMES:
            raise ValueError(f"parser_name 必須在 {VALID_PARSER_NAMES}，收到 {self.parser_name!r}")
        if not isinstance(self.blocked, bool):
            raise TypeError(f"blocked 必須 bool，收到 {type(self.blocked).__name__}")
        if isinstance(self.latency_ms, bool) or not isinstance(self.latency_ms, (int, float)):
            raise TypeError(f"latency_ms 必須數字，收到 {type(self.latency_ms).__name__}")
        if not (self.latency_ms >= 0) or self.latency_ms != self.latency_ms or \
                self.latency_ms in (float("inf"), float("-inf")):
            raise ValueError(f"latency_ms 必須 finite >= 0，收到 {self.latency_ms}")
        for name in ("image_count", "vision_evidence_count", "retry_count"):
            v = getattr(self, name)
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{name} 必須非負 int，收到 {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{name} 不可為負數：{v}")
        # 字串列表不得有空白項（wear_values 允許空字串=wear 缺失語意）
        for name in ("market_hash_names", "item_roles", "conflicts", "warnings"):
            lst = getattr(self, name)
            if not isinstance(lst, list):
                raise TypeError(f"{name} 必須 list")
            for s in lst:
                if not isinstance(s, str) or not s.strip():
                    raise ValueError(f"{name} 含空白項")
        if not isinstance(self.wear_values, list):
            raise TypeError("wear_values 必須 list")
        for s in self.wear_values:
            if not isinstance(s, str):
                raise TypeError(f"wear_values 每項必須 str，收到 {type(s).__name__}")
            if s != "" and not s.strip():
                raise ValueError("wear_values 含空白項")
        if self.fallback_used is not None and not isinstance(self.fallback_used, str):
            raise TypeError(f"fallback_used 必須 str 或 None，收到 {type(self.fallback_used).__name__}")
        # defensive copy
        for name in ("market_hash_names", "item_roles", "wear_values", "conflicts", "warnings"):
            setattr(self, name, copy.deepcopy(getattr(self, name)))
        self.seller_prices = copy.deepcopy(self.seller_prices)
        n_prices = len(self.seller_prices)
        # 對齊驗證：seller_price_item_indexes / currencies / price_indexes / price_types
        for name, lst in (("seller_price_item_indexes", self.seller_price_item_indexes),
                          ("currencies", self.currencies),
                          ("price_indexes", self.price_indexes),
                          ("price_types", self.price_types)):
            if not isinstance(lst, list):
                raise TypeError(f"{name} 必須 list")
            if len(lst) != n_prices:
                raise ValueError(
                    f"{name} 長度 {len(lst)} 必須等於 seller_prices 長度 {n_prices}")
        for x in self.seller_price_item_indexes:
            if isinstance(x, bool) or not isinstance(x, int):
                raise TypeError("seller_price_item_indexes 每項必須非負 int")
            if x < 0:
                raise ValueError("seller_price_item_indexes 不可為負數")
            if x >= len(self.market_hash_names):
                raise ValueError(
                    f"seller_price_item_index {x} 超出 items 範圍 {len(self.market_hash_names)}")
        for c in self.currencies:
            if c is not None and not isinstance(c, Currency):
                raise TypeError(f"currencies 每項必須 Currency 或 None，收到 {type(c).__name__}")
        for s in self.price_types:
            if not isinstance(s, str) or not s.strip():
                raise TypeError("price_types 每項必須非空白 str")
        for x in self.price_indexes:
            if isinstance(x, bool) or not isinstance(x, int):
                raise TypeError("price_indexes 每項必須非負 int")
            if x < 0:
                raise ValueError("price_indexes 不可為負數")
        self.seller_price_item_indexes = copy.deepcopy(self.seller_price_item_indexes)
        self.currencies = copy.deepcopy(self.currencies)
        self.price_types = copy.deepcopy(self.price_types)
        self.price_indexes = copy.deepcopy(self.price_indexes)
        self.linked_pairs = copy.deepcopy(self.linked_pairs)
        # 新欄位驗證
        for name, is_bool_ok in (("stattrak_values", True),):
            lst = getattr(self, name)
            if not isinstance(lst, list):
                raise TypeError(f"{name} 必須 list")
            for v in lst:
                if v is not None and not isinstance(v, bool):
                    raise TypeError(f"{name} 每項必須 bool 或 None")
        for name in ("price_types",):
            lst = getattr(self, name)
            if not isinstance(lst, list):
                raise TypeError(f"{name} 必須 list")
            for s in lst:
                if not isinstance(s, str):
                    raise TypeError(f"{name} 每項必須 str")
        for name in ("price_indexes",):
            lst = getattr(self, name)
            if not isinstance(lst, list):
                raise TypeError(f"{name} 必須 list")
            for x in lst:
                if isinstance(x, bool) or not isinstance(x, int):
                    raise TypeError(f"{name} 每項必須非負 int")
                if x < 0:
                    raise ValueError(f"{name} 不可為負數")
        self.stattrak_values = copy.deepcopy(self.stattrak_values)
        self.price_types = copy.deepcopy(self.price_types)
        self.price_indexes = copy.deepcopy(self.price_indexes)
        itp = list(self.item_to_price_pairs)
        for pair in itp:
            if not isinstance(pair, tuple) or len(pair) != 2:
                raise TypeError("item_to_price_pairs 每項必須 (item_idx, price_idx)")
            for x in pair:
                if isinstance(x, bool) or not isinstance(x, int):
                    raise TypeError("item_to_price_pairs 每項必須 int")
                if x < 0:
                    raise ValueError("item_to_price_pairs 不可為負數")
            if pair[0] >= len(self.market_hash_names):
                raise ValueError(
                    f"item_to_price_pairs item index {pair[0]} 超出 items 範圍")
        self.item_to_price_pairs = copy.deepcopy(itp)

        # items 對齊：若 market_hash_names 非空，wear/role/stattrak 必須 0 或同長
        n_items = len(self.market_hash_names)
        if n_items:
            for name in ("wear_values", "item_roles", "stattrak_values"):
                lst = getattr(self, name)
                if lst and len(lst) != n_items:
                    raise ValueError(
                        f"{name} 長度 {len(lst)} 必須與 items 同長 {n_items}（或空）")
