"""
parse_pipeline.py — deterministic 貼文解析 Pipeline（V2 Phase 5）

流程：RawPostInput → item candidates → price candidates → linking → intent → status

不呼叫：模型 / csgoskins / openskin / Steam / BUFF / Facebook API / 任何外部網路
不執行：匯率換算 / 查價 / 套利計算

Error 策略（方案 B）：捕捉已知 ValueError/TypeError → ParseStatus.ERROR + errors 保存。
API misuse（非 RawPostInput、非 dict）直接拋出。
"""
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemRole
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus
from alkaid_cs2.domain.price_candidate import PriceType
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.parsers.item_parser import parse_item_candidates
from alkaid_cs2.parsers.price_parser import parse_price_candidates
from alkaid_cs2.services.price_item_linker import link_prices_to_items

# warning 產生順序（穩定可測試）
_WARNING_ORDER = (
    "no_items",
    "no_prices",
    "unlinked_items",
    "unlinked_prices",
    "ambiguous_links",
    "bundle_total_deferred",
    "item_without_position",
    "price_without_position",
    "mixed_intent",
)


# ============================================================
# intent 推導（純函式）
# ============================================================
def derive_post_intent(items: list[ItemCandidate]) -> ItemRole:
    """
    規則：
    1. items 空 → UNKNOWN
    2. 全部相同 → 該 role
    3. SELLING+BUYING → UNKNOWN（warning 由 parse_post 補）
    4. TRADE+其他 → TRADE 優先
    5. 只有 REFERENCE/SHOWCASE/DISCUSSION → REFERENCE 優先；只有 SHOWCASE → SHOWCASE
    6. SELLING+其餘 REFERENCE/UNKNOWN → SELLING
    7. BUYING+其餘 REFERENCE/UNKNOWN → BUYING
    8. 其他混合 → UNKNOWN
    """
    if not items:
        return ItemRole.UNKNOWN
    roles = {it.role for it in items}
    if len(roles) == 1:
        return next(iter(roles))
    if {ItemRole.SELLING, ItemRole.BUYING} <= roles:
        return ItemRole.UNKNOWN
    if ItemRole.TRADE in roles:
        return ItemRole.TRADE
    if roles <= {ItemRole.REFERENCE, ItemRole.SHOWCASE, ItemRole.DISCUSSION}:
        if roles == {ItemRole.SHOWCASE}:
            return ItemRole.SHOWCASE
        return ItemRole.REFERENCE
    if ItemRole.SELLING in roles and roles <= {ItemRole.SELLING, ItemRole.REFERENCE, ItemRole.UNKNOWN}:
        return ItemRole.SELLING
    if ItemRole.BUYING in roles and roles <= {ItemRole.BUYING, ItemRole.REFERENCE, ItemRole.UNKNOWN}:
        return ItemRole.BUYING
    return ItemRole.UNKNOWN


def mixed_intent_warning(items: list[ItemCandidate]) -> str | None:
    """回傳 mixed_intent warning；非混合情境回 None。"""
    if not items:
        return None
    roles = {it.role for it in items}
    if len(roles) <= 1:
        return None
    if {ItemRole.SELLING, ItemRole.BUYING} <= roles:
        return "mixed_intent:selling+buying"
    if ItemRole.TRADE in roles:
        return "mixed_intent:trade"
    if roles <= {ItemRole.SELLING, ItemRole.REFERENCE, ItemRole.UNKNOWN}:
        return None  # 規則 6：正常
    if roles <= {ItemRole.BUYING, ItemRole.REFERENCE, ItemRole.UNKNOWN}:
        return None  # 規則 7：正常
    if roles <= {ItemRole.REFERENCE, ItemRole.SHOWCASE, ItemRole.DISCUSSION}:
        return None  # 規則 5：正常
    return "mixed_intent:other"


# ============================================================
# parse_status 推導（純函式）
# ============================================================
def derive_parse_status(
    items: list[ItemCandidate],
    prices: list,
    unlinked_item_indexes: list[int],
    unlinked_price_indexes: list[int],
    errors: list[str],
) -> ParseStatus:
    """
    規則：
    1. errors 非空 → ERROR
    2. items 與 prices 都空 → SKIPPED
    3. 任一 item verified=False 且 validation_error 非空 → UNRESOLVED
    4. 所有 item 有 linked price、所有 price 有 associated（或 BUNDLE_TOTAL）→ OK
    5. 其他 → PARTIAL
    """
    if errors:
        return ParseStatus.ERROR
    if not items and not prices:
        return ParseStatus.SKIPPED
    if any(it.verified is False and it.validation_error for it in items):
        return ParseStatus.UNRESOLVED
    items_ok = all(it.linked_price_indexes for it in items)
    prices_ok = all(
        p.associated_item_index is not None or p.price_type is PriceType.BUNDLE_TOTAL
        for p in prices
    )
    if items_ok and prices_ok:
        return ParseStatus.OK
    return ParseStatus.PARTIAL


# ============================================================
# 主 Pipeline
# ============================================================
def parse_post(
    post: RawPostInput,
    *,
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
) -> ParsedPost:
    # ── 0. 參數驗證（API misuse 直接拋出）──
    if not isinstance(post, RawPostInput):
        raise TypeError(f"post 必須是 RawPostInput，收到 {type(post).__name__}")
    for name, d in (("full_dict", full_dict), ("pattern_dict", pattern_dict),
                    ("weapon_map", weapon_map)):
        if not isinstance(d, dict):
            raise TypeError(f"{name} 必須是 dict，收到 {type(d).__name__}")

    errors: list[str] = []

    # ── 1-4. deterministic 解析（方案 B：捕捉已知錯誤 → ERROR）──
    try:
        items = parse_item_candidates(
            post.raw_text,
            full_dict=full_dict,
            pattern_dict=pattern_dict,
            weapon_map=weapon_map,
        )
        prices = parse_price_candidates(post.raw_text)
        link_result = link_prices_to_items(post.raw_text, items, prices)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
        return ParsedPost(
            post_id=post.post_id,
            author=post.author,
            link=post.link,
            raw_text=post.raw_text,
            image_urls=list(post.image_urls),
            items=[],
            prices=[],
            link_decisions=[],
            parse_status=ParseStatus.ERROR,
            intent=ItemRole.UNKNOWN,
            warnings=[],
            errors=errors,
            unlinked_item_indexes=[],
            unlinked_price_indexes=[],
            source=post.source,
            metadata=dict(post.metadata),
        )

    # 使用 linker 回傳的深拷貝副本
    items_c = link_result.items
    prices_c = link_result.prices
    decisions = link_result.decisions

    # ── 5. intent ──
    intent = derive_post_intent(items_c)
    mixed = mixed_intent_warning(items_c)

    # ── 6-7. warnings（穩定順序、不重複）──
    warnings: list[str] = []
    if not items_c:
        warnings.append("no_items")
    if not prices_c:
        warnings.append("no_prices")
    if link_result.unlinked_item_indexes:
        warnings.append(f"unlinked_items:{len(link_result.unlinked_item_indexes)}")
    if link_result.unlinked_price_indexes:
        warnings.append(f"unlinked_prices:{len(link_result.unlinked_price_indexes)}")
    ambiguous_count = sum(1 for d in decisions if d.ambiguous)
    if ambiguous_count:
        warnings.append(f"ambiguous_links:{ambiguous_count}")
    bundle_count = sum(1 for p in prices_c if p.price_type is PriceType.BUNDLE_TOTAL)
    if bundle_count:
        warnings.append(f"bundle_total_deferred:{bundle_count}")
    no_pos_items = sum(1 for it in items_c if it.match_start is None or it.match_end is None)
    if no_pos_items:
        warnings.append(f"item_without_position:{no_pos_items}")
    no_pos_prices = sum(1 for p in prices_c if p.text_start is None or p.text_end is None)
    if no_pos_prices:
        warnings.append(f"price_without_position:{no_pos_prices}")
    if mixed:
        warnings.append(mixed)
    warnings = list(dict.fromkeys(warnings))  # 去重且保序

    # ── 6. parse_status ──
    status = derive_parse_status(
        items_c, prices_c,
        link_result.unlinked_item_indexes,
        link_result.unlinked_price_indexes,
        errors,
    )

    return ParsedPost(
        post_id=post.post_id,
        author=post.author,
        link=post.link,
        raw_text=post.raw_text,
        image_urls=list(post.image_urls),
        items=items_c,
        prices=prices_c,
        link_decisions=decisions,
        parse_status=status,
        intent=intent,
        warnings=warnings,
        errors=errors,
        unlinked_item_indexes=list(link_result.unlinked_item_indexes),
        unlinked_price_indexes=list(link_result.unlinked_price_indexes),
        source=post.source,
        metadata=dict(post.metadata),
    )
