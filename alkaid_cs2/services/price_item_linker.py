"""
price_item_linker.py — 商品與價格關聯器（V2 Phase 4）

把 ItemCandidate 與 PriceCandidate 依位置、角色、價格型別進行可靠配對。
- 不得呼叫模型 / 外部 API / 匯率換算
- 不得依賴商品名稱字典（只用位置、角色、價格型別）
- 不得原地污染輸入（deepcopy 後回傳副本）
"""
import copy
from dataclasses import dataclass, field

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemRole
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceType
from alkaid_cs2.parsers.item_parser import _SEGMENT_PUNCTS, find_candidate_segment

# 門檻
MIN_LINK_SCORE = 60.0
AMBIGUITY_MARGIN = 15.0

# 價格處理優先順序（高語意優先）
_PRICE_PRIORITY = {
    PriceType.BUNDLE_TOTAL: 0,
    PriceType.SELLER_ASK: 1,
    PriceType.BUFF_FLOOR: 2,
    PriceType.CALCULATED: 3,
    PriceType.REFERENCE: 4,
    PriceType.UNKNOWN: 5,
}

# role/type 加分（不相符為負分）
_ROLE_TYPE_BONUS = {
    (ItemRole.SELLING, PriceType.SELLER_ASK): 40,
    (ItemRole.BUYING, PriceType.SELLER_ASK): 15,   # 暫時兼容：PriceType 尚無 BUY_OFFER
    (ItemRole.REFERENCE, PriceType.REFERENCE): 35,
    (ItemRole.REFERENCE, PriceType.BUFF_FLOOR): 40,
    (ItemRole.BUNDLE, PriceType.BUNDLE_TOTAL): 45,
    (ItemRole.SELLING, PriceType.BUFF_FLOOR): -25,
    (ItemRole.SELLING, PriceType.REFERENCE): -15,
    (ItemRole.REFERENCE, PriceType.SELLER_ASK): -20,
    (ItemRole.TRADE, PriceType.SELLER_ASK): -30,
}


@dataclass
class LinkDecision:
    item_index: int | None
    price_index: int
    score: float
    reason: str
    ambiguous: bool = False

    def __post_init__(self) -> None:
        import math

        # item_index：None 或非負 int（拒絕 bool）
        if isinstance(self.item_index, bool):
            raise TypeError("item_index 不接受 bool")
        if self.item_index is not None:
            if not isinstance(self.item_index, int):
                raise TypeError(f"item_index 必須是 int 或 None，收到 {type(self.item_index).__name__}")
            if self.item_index < 0:
                raise ValueError(f"item_index 不可為負數，收到 {self.item_index}")
        # price_index：非負 int（拒絕 bool）
        if isinstance(self.price_index, bool):
            raise TypeError("price_index 不接受 bool")
        if not isinstance(self.price_index, int):
            raise TypeError(f"price_index 必須是 int，收到 {type(self.price_index).__name__}")
        if self.price_index < 0:
            raise ValueError(f"price_index 不可為負數，收到 {self.price_index}")
        # score：有限 int/float（拒絕 bool/NaN/Inf）
        if isinstance(self.score, bool):
            raise TypeError("score 不接受 bool")
        if not isinstance(self.score, (int, float)):
            raise TypeError(f"score 必須是 int 或 float，收到 {type(self.score).__name__}")
        if not math.isfinite(self.score):
            raise ValueError(f"score 必須是有限數值，收到 {self.score}")
        # reason 不可空白
        if not self.reason or not self.reason.strip():
            raise ValueError("reason 不可空白")
        # ambiguous 必須是 bool
        if not isinstance(self.ambiguous, bool):
            raise TypeError(f"ambiguous 必須是 bool，收到 {type(self.ambiguous).__name__}")
        # ambiguous=True 時 item_index 必須是 None
        if self.ambiguous and self.item_index is not None:
            raise ValueError("ambiguous=True 時 item_index 必須是 None")


@dataclass
class LinkResult:
    items: list[ItemCandidate]
    prices: list[PriceCandidate]
    decisions: list[LinkDecision] = field(default_factory=list)
    unlinked_item_indexes: list[int] = field(default_factory=list)
    unlinked_price_indexes: list[int] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ============================================================
# 輸入驗證
# ============================================================
def _validate_inputs(text, items, prices) -> None:
    if not isinstance(text, str):
        raise TypeError(f"text 必須是 str，收到 {type(text).__name__}")
    for it in items:
        if not isinstance(it, ItemCandidate):
            raise TypeError(f"items 每筆必須是 ItemCandidate，收到 {type(it).__name__}")
    for p in prices:
        if not isinstance(p, PriceCandidate):
            raise TypeError(f"prices 每筆必須是 PriceCandidate，收到 {type(p).__name__}")
    # 位置範圍驗證
    for it in items:
        for attr in ("match_start", "match_end"):
            v = getattr(it, attr)
            if v is not None and not (0 <= v <= len(text)):
                raise ValueError(f"item {attr}={v} 超出 text 範圍({len(text)})")
    for p in prices:
        for attr in ("text_start", "text_end"):
            v = getattr(p, attr)
            if v is not None and not (0 <= v <= len(text)):
                raise ValueError(f"price {attr}={v} 超出 text 範圍({len(text)})")
    # 重複物件拒絕
    item_ids = [id(it) for it in items]
    if len(set(item_ids)) != len(item_ids):
        raise ValueError("items 不得包含重複物件")
    price_ids = [id(p) for p in prices]
    if len(set(price_ids)) != len(price_ids):
        raise ValueError("prices 不得包含重複物件")


# ============================================================
# 配對計分
# ============================================================
def _role_type_bonus(role: ItemRole, ptype: PriceType) -> float:
    return float(_ROLE_TYPE_BONUS.get((role, ptype), 0))


def _score_pair(
    text: str,
    item: ItemCandidate,
    price: PriceCandidate,
    item_spans: list[tuple[int, int]],
    segment: tuple[int, int],
) -> float:
    """單一 price → item 的 linking score。"""
    if item.match_start is None or item.match_end is None:
        return 0.0  # 無位置 → 不猜測（低信心）
    if price.text_start is None or price.text_end is None:
        return 0.0

    score = 0.0
    seg_start, seg_end = segment
    price_pos = price.text_start
    price_end = price.text_end
    item_center = (item.match_start + item.match_end) / 2.0
    in_segment = seg_start <= price_pos < seg_end

    # 不在商品 segment → -60，不得再取得 +100/+55（跨句/跨換行防護）
    if not in_segment:
        score -= 60
    else:
        # 下一個商品開始（價格在 item 之後且在下一個 item 之前 → +100）
        next_start = min(
            (s for s, e in item_spans if s >= item.match_end),
            default=None,
        )
        if item.match_end <= price_pos and (next_start is None or price_pos < next_start):
            score += 100
        elif price_pos < item.match_start:
            score += 55
        else:
            score += 40

    # 距離懲罰（每字 -1，最多 -40）
    dist = abs(price_pos - item_center)
    score -= min(dist, 40)

    # 價格跨越另一個商品候選 → -80
    for s, e in item_spans:
        if (s, e) == (item.match_start, item.match_end):
            continue
        if price_pos < e and price_end > s:
            score -= 80
            break

    # role/type 相符
    score += _role_type_bonus(item.role, price.price_type)
    return score


def _reason_for(score: float, role: ItemRole, ptype: PriceType) -> str:
    parts = [f"score={score:.0f}", f"role={role.value}", f"type={ptype.value}"]
    if role is ItemRole.BUYING and ptype is PriceType.SELLER_ASK:
        parts.append("(暫時兼容: PriceType 尚無 BUY_OFFER)")
    return " ".join(parts)


def _is_after_last_item(
    price: PriceCandidate,
    item: ItemCandidate,
    item_spans: list[tuple[int, int]],
) -> bool:
    """價格是否在『最後一個商品』之後（該商品後無其他候選）。"""
    if item.match_end is None or price.text_start is None:
        return False
    next_start = min(
        (s for s, e in item_spans if s >= item.match_end),
        default=None,
    )
    return next_start is None and price.text_start >= item.match_end


# ============================================================
# 主函式
# ============================================================
def link_prices_to_items(
    text: str,
    items: list[ItemCandidate],
    prices: list[PriceCandidate],
) -> LinkResult:
    _validate_inputs(text, items, prices)

    # 深拷貝：不污染呼叫端輸入
    items_c = copy.deepcopy(items)
    prices_c = copy.deepcopy(prices)

    # 無商品可配（empty items）
    if not items_c:
        return LinkResult(
            items=[],
            prices=prices_c,
            decisions=[],
            unlinked_item_indexes=[],
            unlinked_price_indexes=list(range(len(prices_c))),
            warnings=[],
        )

    # 只包含有效位置的 span（無位置 item 不影響 next_start/跨商品判斷）
    item_spans = [
        (it.match_start, it.match_end)
        for it in items_c
        if it.match_start is not None and it.match_end is not None
    ]
    # 每商品 segment（重用 Phase 3.1 邏輯）
    segments: dict[int, tuple[int, int]] = {}
    for i, it in enumerate(items_c):
        if it.match_start is not None and it.match_end is not None:
            segments[i] = find_candidate_segment(
                text, it.match_start, it.match_end, item_spans
            )
        else:
            segments[i] = (0, len(text))

    decisions: list[LinkDecision] = []
    warnings: list[str] = []

    # 價格處理順序：BUNDLE → SELLER_ASK → BUFF_FLOOR → CALCULATED → REFERENCE → UNKNOWN
    price_order = sorted(
        range(len(prices_c)),
        key=lambda i: _PRICE_PRIORITY.get(prices_c[i].price_type, 9),
    )

    for pi in price_order:
        price = prices_c[pi]

        # ── BUNDLE_TOTAL 特殊處理：不綁單一商品 ──
        if price.price_type is PriceType.BUNDLE_TOTAL:
            decisions.append(LinkDecision(
                item_index=None, price_index=pi, score=0.0,
                reason="bundle_total: 不直接綁定單一商品，由 ParsedPost 層處理 bundle_items",
            ))
            warnings.append(
                f"price[{pi}] 是 bundle_total，未綁定單一商品（後續 ParsedPost 層處理）"
            )
            continue

        # 對全部商品算分
        scored: list[tuple[float, int]] = []
        for ii in range(len(items_c)):
            s = _score_pair(text, items_c[ii], price, item_spans, segments[ii])
            scored.append((s, ii))
        scored.sort(key=lambda x: x[0], reverse=True)

        top_score, top_i = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -999.0

        # 門檻
        if top_score < MIN_LINK_SCORE:
            decisions.append(LinkDecision(
                item_index=None, price_index=pi, score=top_score,
                reason=f"低於最低門檻({MIN_LINK_SCORE:.0f})，未連結 "
                       + _reason_for(top_score, items_c[top_i].role, price.price_type),
            ))
            continue

        # 孤兒價格歧義：價格數 < 商品數 且 價格在最後商品後、價格前有多個同段未配商品
        # → 無法確定歸屬，保守不綁定（規格案例 8）
        if len(prices_c) < len(items_c) and _is_after_last_item(price, items_c[top_i], item_spans):
            unassigned_before: list[int] = []
            for ii in range(len(items_c)):
                it = items_c[ii]
                if it.linked_price_indexes:
                    continue
                if it.match_end is None or price.text_start is None:
                    continue
                if it.match_end > price.text_start:
                    continue  # 在價格之後
                gap = text[it.match_end:price.text_start]
                if any(p in gap for p in _SEGMENT_PUNCTS):
                    continue  # 被標點/換行隔開
                unassigned_before.append(ii)
            if len(unassigned_before) >= 2:
                decisions.append(LinkDecision(
                    item_index=None, price_index=pi, score=top_score,
                    reason=f"ambiguous: 最後價格前有 {len(unassigned_before)} 個同段未配對商品，無法確定歸屬",
                    ambiguous=True,
                ))
                warnings.append(
                    f"price[{pi}] 為孤兒價格（同段未配商品 {unassigned_before}），保守不綁定"
                )
                continue

        # 歧義：前二名分差過小
        if top_score - second_score < AMBIGUITY_MARGIN:
            decisions.append(LinkDecision(
                item_index=None, price_index=pi, score=top_score,
                reason=f"ambiguous: 前二名分差 {top_score - second_score:.1f} < {AMBIGUITY_MARGIN:.0f}",
                ambiguous=True,
            ))
            warnings.append(
                f"price[{pi}] 配對歧義（top={top_score:.0f} second={second_score:.0f}），未自動綁定"
            )
            continue

        # 正式綁定
        items_c[top_i].linked_price_indexes.append(pi)
        price.associated_item_index = top_i
        decisions.append(LinkDecision(
            item_index=top_i, price_index=pi, score=top_score,
            reason=_reason_for(top_score, items_c[top_i].role, price.price_type),
        ))

    # linked_price_indexes 遞增排序且不重複
    for it in items_c:
        it.linked_price_indexes = sorted(set(it.linked_price_indexes))

    # 未連結清單
    unlinked_items = [
        i for i, it in enumerate(items_c) if not it.linked_price_indexes
    ]
    unlinked_prices = [
        i for i, p in enumerate(prices_c) if p.associated_item_index is None
    ]

    return LinkResult(
        items=items_c,
        prices=prices_c,
        decisions=decisions,
        unlinked_item_indexes=unlinked_items,
        unlinked_price_indexes=unlinked_prices,
        warnings=warnings,
    )
