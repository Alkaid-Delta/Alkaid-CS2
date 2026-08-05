"""
legacy_adapter.py — 新舊介面 Adapter（V2 Phase 6.1）

把 V2 ParsedPost / parse_post() 轉換成舊版 extract_skin_info() 可接受的輸出，
讓 analyze_arbitrage.py 後續能逐步切換。

安全規則：
- 多商品不可盲目降級成第一件 → blocked
- 非 TWD 不在 adapter 換算 → blocked（Phase 6.2/6.3 經 CurrencyService）
- 不呼叫模型 / 外部 API / 查價
"""
from dataclasses import dataclass, field
from enum import Enum
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.item_candidate import ItemRole
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus
from alkaid_cs2.domain.price_candidate import PriceType
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.pipeline.parse_pipeline import parse_post


class LegacySelectionReason(str, Enum):
    SINGLE_SELLING_ITEM = "single_selling_item"
    HIGHEST_CONFIDENCE = "highest_confidence"
    ONLY_LINKED_SELLER_PRICE = "only_linked_seller_price"
    COMPATIBILITY_FALLBACK = "compatibility_fallback"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"
    NO_ITEM = "no_item"
    NO_PRICE = "no_price"


@dataclass
class LegacyAdapterResult:
    legacy_data: dict[str, object] | None
    selected_item_index: int | None
    selected_price_index: int | None
    selection_reason: LegacySelectionReason
    warnings: list[str] = field(default_factory=list)
    blocked: bool = False
    parsed_post: "ParsedPost | None" = None  # P6-R1-E2：text-only V2 structured path 保留

    def __post_init__(self) -> None:
        if self.legacy_data is not None:
            if not isinstance(self.legacy_data, dict):
                raise TypeError(f"legacy_data 必須是 dict 或 None，收到 {type(self.legacy_data).__name__}")
            mhn = self.legacy_data.get("market_hash_name")
            if not isinstance(mhn, str) or not mhn.strip():
                raise ValueError("legacy_data 的 market_hash_name 必須是非空 str")
            if "seller_price" not in self.legacy_data:
                raise ValueError("legacy_data 必須包含 seller_price")
            sp = self.legacy_data["seller_price"]
            if isinstance(sp, bool) or (sp is not None and not isinstance(sp, (int, Decimal))):
                raise TypeError(f"seller_price 必須是 int/Decimal/None，收到 {type(sp).__name__}")
            # Phase P1.2：typed original 一致性（不一致 fail-closed）
            orig = self.legacy_data.get("original")
            if orig is not None:
                from alkaid_cs2.domain.price import Money
                if type(orig) is not Money:
                    raise TypeError("original 必須是 Money")
                if self.legacy_data.get("original_price") is not None and \
                        self.legacy_data["original_price"] != orig.amount:
                    raise ValueError("original_price 與 original.amount 不一致")
                if self.legacy_data.get("currency") is not None and \
                        self.legacy_data["currency"] != orig.currency.value:
                    raise ValueError("currency 與 original.currency 不一致")
            conv = self.legacy_data.get("converted")
            if conv is not None:
                from alkaid_cs2.domain.price import ConvertedMoney
                if type(conv) is not ConvertedMoney:
                    raise TypeError("converted 必須是 ConvertedMoney")
                if orig is not None and conv.original != orig:
                    raise ValueError("converted.original 與 original 不一致")
            if "confidence" not in self.legacy_data:
                raise ValueError("legacy_data 必須包含 confidence")
        if not self.blocked and self.legacy_data is None:
            raise ValueError("blocked=False 時 legacy_data 不得為 None")
        for attr in ("selected_item_index", "selected_price_index"):
            v = getattr(self, attr)
            if v is None:
                continue
            if isinstance(v, bool) or not isinstance(v, int):
                raise TypeError(f"{attr} 必須是非負 int 或 None，收到 {type(v).__name__}")
            if v < 0:
                raise ValueError(f"{attr} 不可為負數，收到 {v}")
        if not isinstance(self.selection_reason, LegacySelectionReason):
            raise TypeError("selection_reason 必須是 LegacySelectionReason enum")
        if not isinstance(self.warnings, list):
            raise TypeError("warnings 必須是 list")
        if any(not isinstance(w, str) or not w.strip() for w in self.warnings):
            raise ValueError("warnings 不得含空白字串")
        self.warnings = list(self.warnings)  # defensive copy
        if not isinstance(self.blocked, bool):
            raise TypeError("blocked 必須是 bool")
        if self.blocked and self.legacy_data is not None:
            raise ValueError("blocked=True 時 legacy_data 必須為 None")


def _blocked(reason: LegacySelectionReason, warnings: list[str]) -> LegacyAdapterResult:
    return LegacyAdapterResult(
        legacy_data=None,
        selected_item_index=None,
        selected_price_index=None,
        selection_reason=reason,
        warnings=list(dict.fromkeys(warnings)),
        blocked=True,
    )


def _to_legacy_amount(amount: Decimal) -> Decimal | int:
    """轉成 legacy seller_price 型別（Decimal|int，P1.2 不轉 float）。

    domain/adapter 始終保留 Decimal；只有最終顯示層才量化。
    """
    if amount == amount.to_integral_value():
        return int(amount)
    return amount


def to_legacy_skin_info(parsed_post: ParsedPost) -> LegacyAdapterResult:
    """ParsedPost → 舊版 extract_skin_info() 相容輸出。"""
    if not isinstance(parsed_post, ParsedPost):
        raise TypeError(f"parsed_post 必須是 ParsedPost，收到 {type(parsed_post).__name__}")

    # ── 1. ERROR ──
    if parsed_post.parse_status is ParseStatus.ERROR:
        warnings = ["parse_error"] + list(parsed_post.errors)
        return _blocked(LegacySelectionReason.UNRESOLVED, warnings)

    # ── 2. UNRESOLVED ──
    if parsed_post.parse_status is ParseStatus.UNRESOLVED:
        return _blocked(LegacySelectionReason.UNRESOLVED, ["unresolved"])

    # ── 3. 無 items ──
    if not parsed_post.items:
        return _blocked(LegacySelectionReason.NO_ITEM, ["no_items"])

    warnings: list[str] = []

    # ── 4. 先選商品（role + SELLER_ASK），後驗證被選商品 ──
    def seller_asks(item_idx: int) -> list[int]:
        return [
            j for j in parsed_post.items[item_idx].linked_price_indexes
            if parsed_post.prices[j].price_type is PriceType.SELLER_ASK
        ]

    selling = [i for i in range(len(parsed_post.items))
               if parsed_post.items[i].role is ItemRole.SELLING]

    # 例外：唯一 item 且有 SELLER_ASK 價格 → 視為 selling
    # （解決「售 X 同磨底...」中 detect_role 因「同磨底」最近而誤判 REFERENCE）
    # SELLER_ASK 型別本身由 price_parser 的售/賣/算語境產生，強烈暗示賣家
    if not selling and len(parsed_post.items) == 1 and seller_asks(0):
        selling = [0]

    if not selling:
        # E：無 selling role → 不得硬猜
        return _blocked(LegacySelectionReason.AMBIGUOUS, ["no_selling_item"])

    with_ask = [i for i in selling if seller_asks(i)]

    if len(with_ask) > 1:
        # D：多件都有 seller ask → 無法安全降級
        return _blocked(LegacySelectionReason.AMBIGUOUS,
                        [f"multiple_selling_items_with_seller_ask:{len(with_ask)}"])
    if len(with_ask) == 1:
        sel_item = with_ask[0]
        sel_reason = (LegacySelectionReason.SINGLE_SELLING_ITEM
                      if len(selling) == 1
                      else LegacySelectionReason.ONLY_LINKED_SELLER_PRICE)
    elif len(selling) == 1:
        # 唯一 selling 但無 SELLER_ASK → 可輸出名稱，seller_price=None
        sel_item = selling[0]
        sel_reason = LegacySelectionReason.COMPATIBILITY_FALLBACK
    else:
        return _blocked(LegacySelectionReason.AMBIGUOUS,
                        ["no_seller_ask_for_multi_items"])

    # ── 3. 未選中 item 的 validation_error → warning（不阻擋）──
    for i in range(len(parsed_post.items)):
        if i != sel_item and parsed_post.items[i].validation_error:
            warnings.append(f"unselected_item[{i}]_validation_error")

    # ── 3b. 驗證被選商品 ──
    item = parsed_post.items[sel_item]
    if item.validation_error:
        return _blocked(LegacySelectionReason.UNRESOLVED,
                        [f"item[{sel_item}]_validation_error:{item.validation_error}"])
    if not item.market_hash_name:
        return _blocked(LegacySelectionReason.UNRESOLVED,
                        [f"item[{sel_item}]_missing_market_hash_name"])

    asks = seller_asks(sel_item)

    # 存在 UNKNOWN 價格但未使用 → 標記（不得當 seller ask）
    has_unknown = any(
        parsed_post.prices[j].price_type is PriceType.UNKNOWN
        for j in item.linked_price_indexes
    )
    if has_unknown and not asks:
        warnings.append("unknown_price_not_used")

    # ── 5. 價格選擇（只收 SELLER_ASK）──
    if not asks:
        legacy = {
            "market_hash_name": item.market_hash_name,
            # Phase P1.3：無價格不虛構幣別
            "currency": None,
            "original": None,
            "original_price": None,
            "converted": None,
            "seller_price": None,
            "confidence": item.confidence,
            "source": "v2_adapter",
            "item_role": item.role.value,
            "selection_reason": sel_reason.value,
            # Phase P2：透傳 verification metadata（嚴格 bool）
            "verified": item.verified,
            "verified_by": item.verified_by,
            "validation_error": item.validation_error,
        }
        warnings.append("no_seller_price")
        return LegacyAdapterResult(
            legacy_data=legacy,
            selected_item_index=sel_item,
            selected_price_index=None,
            selection_reason=sel_reason,
            warnings=list(dict.fromkeys(warnings)),
            blocked=False,
            parsed_post=parsed_post,
        )

    ask_prices = [parsed_post.prices[j] for j in asks]
    amounts = {p.money.amount for p in ask_prices}
    if len(amounts) > 1:
        return _blocked(LegacySelectionReason.AMBIGUOUS,
                        [f"conflicting_seller_prices:{len(amounts)}"])
    if len(ask_prices) > 1:
        warnings.append(f"duplicate_seller_price:{len(ask_prices)}")

    price = ask_prices[0]
    sel_price_idx = asks[0]

    # ── 6. 貨幣（P1.2：adapter 不執行換算、不判斷幣別──
    # 原始 Money/currency 完整透傳，由共用 conversion stage 統一處理：
    # RMB/USD → CurrencyService 一次；UNKNOWN → stage fail-closed）──

    # ── 8. legacy_data 格式 ──
    legacy = {
        "market_hash_name": item.market_hash_name,
        # Phase P1.2：完整透傳原始 Money／currency（adapter 不換算）
        "original": price.money,
        "original_price": price.money.amount,
        "currency": price.money.currency.value,
        "converted": price.converted,
        "seller_price": _to_legacy_amount(price.money.amount),
        "confidence": item.confidence,
        # Phase P2：透傳 verification metadata（嚴格 bool）
        "verified": item.verified,
        "verified_by": item.verified_by,
        "validation_error": item.validation_error,
        "source": "v2_adapter",
        "item_role": item.role.value,
        "selection_reason": sel_reason.value,
    }
    return LegacyAdapterResult(
        legacy_data=legacy,
        selected_item_index=sel_item,
        selected_price_index=sel_price_idx,
        selection_reason=sel_reason,
        warnings=list(dict.fromkeys(warnings)),
        blocked=False,
        parsed_post=parsed_post,
    )


def parse_to_legacy(
    post: RawPostInput,
    *,
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
) -> LegacyAdapterResult:
    """RawPostInput → parse_post() → to_legacy_skin_info()。"""
    parsed = parse_post(
        post,
        full_dict=full_dict,
        pattern_dict=pattern_dict,
        weapon_map=weapon_map,
    )
    return to_legacy_skin_info(parsed)
