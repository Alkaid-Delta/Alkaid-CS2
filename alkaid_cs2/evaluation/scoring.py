"""
scoring.py — 比對與計分（Phase 6.4A-6.4B.1）

Item matching / seller price 分項比對 / raw & production safe matrix。
禁止貨幣換算。
"""
from dataclasses import dataclass, field
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.evaluation.models import EvaluationCase, ExpectedItem
from alkaid_cs2.evaluation.prediction import EvaluationPrediction

# 簡繁 alias（只用於 comparator，不改正式字典）
_ALIAS = {
    "戰痕累累": "战痕累累", "久經沙場": "久经沙场", "嶄新出廠": "崭新出厂",
    "略有磨損": "略有磨损", "破損不堪": "破损不堪",
}


def _norm(s: str | None) -> str:
    if not s:
        return ""
    s = s.strip().lower()
    return _ALIAS.get(s, s)


@dataclass
class MetricCounts:
    true_positive: int = 0
    false_positive: int = 0
    false_negative: int = 0
    true_negative: int = 0

    def precision(self) -> float:
        d = self.true_positive + self.false_positive
        return self.true_positive / d if d else 0.0

    def recall(self) -> float:
        d = self.true_positive + self.false_negative
        return self.true_positive / d if d else 0.0

    def f1(self) -> float:
        p, r = self.precision(), self.recall()
        d = p + r
        return 2 * p * r / d if d else 0.0

    def accuracy(self) -> float:
        d = self.true_positive + self.false_positive + \
            self.false_negative + self.true_negative
        return (self.true_positive + self.true_negative) / d if d else 0.0


# ============================================================
# Item matching
# ============================================================
@dataclass
class ItemMatch:
    expected_idx: int
    predicted_idx: int
    level: str  # exact / partial


def _split_mhn(mhn: str) -> tuple[str, str, bool]:
    """mhn → (weapon, skin, stattrak)。解析 StatTrak™ / ★ 前綴。

    "StatTrak™ AK-47 | Redline" → (AK-47, Redline, True)
    "★ StatTrak™ Karambit | Doppler" → (Karambit, Doppler, True)
    "★ Karambit | Doppler" → (Karambit, Doppler, False)
    """
    if not mhn:
        return "", "", False
    body = mhn.strip()
    stattrak = False
    # 先移除 ★ 前綴
    if body.startswith("★"):
        body = body.lstrip("★").strip()
    # 再檢查 StatTrak 前綴（可能帶 ™）
    low = body.lower()
    if low.startswith("stattrak") or low.startswith("stattrak™") or \
            "stattrak" in low[:12]:
        stattrak = True
        # 移除 StatTrak™ 前綴：找第一個 "|" 前的部分做字首清除
        if "|" in body:
            head, _, rest = body.partition("|")
            # head 可能 "StatTrak™ AK-47" 或 "StatTrak™ Karambit"
            head_parts = head.split()
            if head_parts and head_parts[0].lower().startswith("stattrak"):
                head = " ".join(head_parts[1:])
            body = f"{head.strip()} | {rest.strip()}"
        else:
            # 無 |：清掉字首
            parts = body.split()
            if parts and parts[0].lower().startswith("stattrak"):
                body = " ".join(parts[1:])
    if " | " in body:
        weapon, _, skin_part = body.partition(" | ")
        skin = skin_part.split(" (")[0]
        return weapon.strip(), skin.strip(), stattrak
    return "", body.strip(), stattrak


def _component_score(expected: ExpectedItem, weapon: str, skin: str,
                     wear: str, stattrak: bool | None) -> int:
    """0=不匹配 1=skin-only 2=partial(wear/stattrak 缺失) 3=exact(component)。"""
    e_weapon, e_skin = _norm(expected.weapon), _norm(expected.skin)
    p_weapon, p_skin = _norm(weapon), _norm(skin)
    if e_skin and p_skin and e_skin != p_skin:
        return 0
    if e_weapon and p_weapon and e_weapon != p_weapon:
        return 0
    if not (e_skin and p_skin):
        return 0
    # stattrak 明確衝突 → 不匹配
    if expected.stattrak is not None and stattrak is not None and \
            expected.stattrak != stattrak:
        return 0
    e_wear, p_wear = _norm(expected.wear), _norm(wear)
    if e_wear and p_wear:
        if e_wear == p_wear and expected.stattrak == stattrak:
            return 3 if (e_weapon and p_weapon and e_weapon == p_weapon) else 2
        return 0 if (e_wear != p_wear) else 2
    if e_weapon and p_weapon:
        return 2
    return 1  # skin-only


def match_expected_items(
    expected: list[ExpectedItem],
    predicted: EvaluationPrediction,
) -> tuple[list[ItemMatch], list[int], list[int]]:
    """deterministic 匹配：exact 優先 → partial 次之 → stable index。

    回傳 (matches, expected_unmatched, predicted_unmatched)。
    """
    matches: list[ItemMatch] = []
    expected_used: set[int] = set()
    predicted_used: set[int] = set()

    pred_items: list[tuple[int, str, str, str, str, bool | None]] = []
    for idx, mhn in enumerate(predicted.market_hash_names):
        weapon, skin, mhn_stattrak = _split_mhn(mhn)
        # prediction.stattrak_values 優先（mhn 解析為 fallback）
        st = None
        if idx < len(predicted.stattrak_values):
            st = predicted.stattrak_values[idx]
        if st is None:
            st = mhn_stattrak  # False 也是明確值（不得 or None 丟失）
        wear = predicted.wear_values[idx] if idx < len(predicted.wear_values) else ""
        pred_items.append((idx, mhn, weapon, skin, wear, st))

    # Round 1: exact（mhn case-insensitive 相同）
    for p_idx, mhn, weapon, skin, wear, st in pred_items:
        if p_idx in predicted_used:
            continue
        for e_idx, item in enumerate(expected):
            if e_idx in expected_used:
                continue
            if item.market_hash_name and _norm(item.market_hash_name) == _norm(mhn):
                # mhn 相同但 stattrak 明確衝突 → 不 exact
                if item.stattrak is not None and st is not None and \
                        item.stattrak != st:
                    continue
                matches.append(ItemMatch(e_idx, p_idx, "exact"))
                expected_used.add(e_idx)
                predicted_used.add(p_idx)
                break

    # Round 2: exact（component 全等）
    for p_idx, mhn, weapon, skin, wear, st in pred_items:
        if p_idx in predicted_used:
            continue
        for e_idx, item in enumerate(expected):
            if e_idx in expected_used:
                continue
            if _component_score(item, weapon, skin, wear, st) == 3:
                matches.append(ItemMatch(e_idx, p_idx, "exact"))
                expected_used.add(e_idx)
                predicted_used.add(p_idx)
                break

    # Round 3: partial（weapon+skin 相同、wear/stattrak 一方缺失）
    for p_idx, mhn, weapon, skin, wear, st in pred_items:
        if p_idx in predicted_used:
            continue
        for e_idx, item in enumerate(expected):
            if e_idx in expected_used:
                continue
            if _component_score(item, weapon, skin, wear, st) == 2:
                matches.append(ItemMatch(e_idx, p_idx, "partial"))
                expected_used.add(e_idx)
                predicted_used.add(p_idx)
                break

    expected_unmatched = [i for i in range(len(expected)) if i not in expected_used]
    predicted_unmatched = [i for i in range(len(predicted.market_hash_names))
                           if i not in predicted_used]
    return matches, expected_unmatched, predicted_unmatched


# ============================================================
# Price 對齊（統一 helper）
# ============================================================
def get_seller_price_for_item(prediction: EvaluationPrediction, item_index: int):
    """item index → (price, currency, price_index) | None。

    currency 必須透過 seller_price_item_indexes 找到該 item 的 seller price，
    再取同一 seller price 位置的 currency（禁止 prediction.currencies[item_index]）。
    """
    for i, idx in enumerate(prediction.seller_price_item_indexes):
        if idx == item_index and i < len(prediction.seller_prices):
            cur = prediction.currencies[i] if i < len(prediction.currencies) else None
            pi = prediction.price_indexes[i] if i < len(prediction.price_indexes) else None
            return prediction.seller_prices[i], cur, pi
    return None


@dataclass
class PriceMatchResult:
    correct_seller_ask: int = 0
    missed_seller_ask: int = 0
    wrong_amount: int = 0
    wrong_currency: int = 0
    false_seller_asks: int = 0  # alias → extra_unmatched_seller_asks
    reference_promoted_to_seller: int = 0  # alias → seller_negative_item_false_positives
    seller_ask_on_wrong_item: int = 0
    seller_negative_item_false_positives: int = 0
    extra_unmatched_seller_asks: int = 0
    negative_opportunities: int = 0

    @property
    def wrong_total(self) -> int:
        return self.missed_seller_ask + self.wrong_amount + self.wrong_currency

    @property
    def false_total(self) -> int:
        return self.seller_negative_item_false_positives


def match_seller_prices(
    expected: list[ExpectedItem],
    predicted: EvaluationPrediction,
    matches: list[ItemMatch],
) -> PriceMatchResult:
    """seller price 分項比對（禁止換算）。"""
    r = PriceMatchResult()
    # negative opportunities：expected 中 seller_price is None 的 items 數
    r_neg = sum(1 for it in expected if it.seller_price is None)
    # expected 有 seller_price 的 items
    for e_idx, item in enumerate(expected):
        if item.seller_price is None:
            continue
        m = next((m for m in matches if m.expected_idx == e_idx), None)
        if m is None:
            r.missed_seller_ask += 1
            continue
        pv = get_seller_price_for_item(predicted, m.predicted_idx)
        if pv is None:
            r.missed_seller_ask += 1
            continue
        p_price, p_cur, _ = pv
        if p_cur == item.currency and p_price == item.seller_price:
            r.correct_seller_ask += 1
        elif p_cur != item.currency:
            r.wrong_currency += 1
        else:
            r.wrong_amount += 1
    # predicted 的 seller ask 對應 item（6.4B.5 去重）：
    # - matched 且 GT 無 seller → negative item FP（依 expected item 去重，
    #   同一 negative item 多筆 ask 只計 1 次，不得 > negative_opportunities）
    # - unmatched 且 GT 有 seller ask 語境 → seller_ask_on_wrong_item（獨立）
    # - unmatched 且 GT 全無 seller ask → extra_unmatched_seller_asks（獨立，可按筆數計）
    matched_p = {m.predicted_idx for m in matches}
    gt_has_seller_ask = any(it.seller_price is not None for it in expected)
    negative_item_fp_indexes: set[int] = set()
    for item_idx in predicted.seller_price_item_indexes:
        if item_idx in matched_p:
            m = next((m for m in matches if m.predicted_idx == item_idx), None)
            if m is not None and expected[m.expected_idx].seller_price is None:
                negative_item_fp_indexes.add(m.expected_idx)
        else:
            if gt_has_seller_ask:
                r.seller_ask_on_wrong_item += 1
            else:
                r.extra_unmatched_seller_asks += 1
                r.false_seller_asks += 1  # 相容 alias
    r.seller_negative_item_false_positives = len(negative_item_fp_indexes)
    r.reference_promoted_to_seller = r.seller_negative_item_false_positives  # alias 去重後
    r.negative_opportunities = r_neg
    return r


# ============================================================
# Safe gate（raw 與 production 分開）
# ============================================================
def is_prediction_safe(prediction: EvaluationPrediction) -> bool:
    """統一 Safe 判定（6.4B.4）。

    parsed + not blocked + source != skipped。
    unresolved/error 等 parse_status 非 "parsed" → 不 safe。
    """
    return (
        not prediction.blocked
        and prediction.parse_status == "parsed"
        and prediction.source != "skipped"
    )


def evaluate_safe_decision(expected_safe: bool | None,
                           prediction: EvaluationPrediction) -> dict[str, object]:
    """predicted_safe 由 is_prediction_safe 統一判定。

    expected_safe=None（raw 無標註）→ 不計入 confusion matrix。
    """
    predicted_safe = is_prediction_safe(prediction)
    if expected_safe is None:
        return {
            "expected_safe": None, "predicted_safe": predicted_safe,
            "excluded": True,
            "safe_true_positive": 0, "safe_false_positive": 0,
            "safe_false_negative": 0, "safe_true_negative": 0,
        }
    return {
        "expected_safe": expected_safe,
        "predicted_safe": predicted_safe,
        "excluded": False,
        "safe_true_positive": int(expected_safe and predicted_safe),
        "safe_false_positive": int((not expected_safe) and predicted_safe),
        "safe_false_negative": int(expected_safe and (not predicted_safe)),
        "safe_true_negative": int((not expected_safe) and (not predicted_safe)),
    }


# ============================================================
# Case 層計分
# ============================================================
@dataclass
class CaseEvaluationResult:
    case_id: str
    parser_name: str
    expected_safe: bool | None
    predicted_safe: bool
    image_kind_correct: bool | None = None
    item_exact_matches: int = 0
    item_partial_matches: int = 0
    item_false_positives: int = 0
    item_false_negatives: int = 0
    seller_price_exact_matches: int = 0
    seller_price_missed: int = 0
    seller_price_wrong_amount: int = 0
    seller_price_wrong_currency: int = 0
    false_seller_asks: int = 0
    reference_promoted_to_seller: int = 0
    seller_ask_on_wrong_item: int = 0
    seller_negative_item_false_positives: int = 0
    extra_unmatched_seller_asks: int = 0
    seller_price_negative_opportunities: int = 0
    currency_exact_matches: int = 0
    currency_wrong: int = 0
    wear_exact_matches: int = 0
    wear_wrong: int = 0
    linking_correct: int = 0
    linking_wrong: int = 0
    conflict_expected: bool = False
    conflict_detected: bool = False
    fallback_used: str | None = None
    latency_ms: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def seller_price_wrong(self) -> int:
        return self.seller_price_missed + self.seller_price_wrong_amount + \
            self.seller_price_wrong_currency

    @property
    def seller_price_false_total(self) -> int:
        return self.false_seller_asks + self.reference_promoted_to_seller


_EXPECTED_TO_VISION_KIND = {
    "inventory": "inventory_grid", "single": "single_item",
    "multi": "multi_item", "market": "market_listing",
    "chat": "chat_screenshot", "inspect": "inspect_screenshot",
    "payment": "payment_proof", "trade": "trade_confirmation",
    "other": "other", "unknown": "unknown",
}


def score_case(case: EvaluationCase, parser_name: str,
               prediction: EvaluationPrediction,
               raw_merge=None,
               expected_safe: bool | None = None) -> CaseEvaluationResult:
    """從 prediction 建立 CaseEvaluationResult。

    - expected_safe：由呼叫端明確傳入（production parser 用
      case.expected_safe_for_production；vision_raw 用 case.expected_raw_vision_safe，
      可為 None 表示不納入 raw safe matrix）。None 不得 fallback。
    - raw_merge 只提供圖片分類/衝突資訊（僅 vision_raw 傳入）。
    """
    matches, expected_unmatched, predicted_unmatched = match_expected_items(
        case.expected_items, prediction)
    exact = sum(1 for m in matches if m.level == "exact")
    partial = sum(1 for m in matches if m.level == "partial")

    # wear / currency（對 exact+partial matches；currency 走 seller price 對應）
    wear_ok = wear_wrong = cur_ok = cur_wrong = 0
    for m in matches:
        item = case.expected_items[m.expected_idx]
        p_idx = m.predicted_idx
        p_wear = prediction.wear_values[p_idx] if p_idx < len(prediction.wear_values) else None
        if item.wear:
            if p_wear and _norm(p_wear) == _norm(item.wear):
                wear_ok += 1
            else:
                wear_wrong += 1
        pv = get_seller_price_for_item(prediction, p_idx)
        if item.currency and pv is not None:
            if pv[1] == item.currency:
                cur_ok += 1
            else:
                cur_wrong += 1

    # price（分項）
    pr = match_seller_prices(case.expected_items, prediction, matches)

    # linking：seller price 的 associated item 必須等於 matched predicted item
    linking_ok = linking_wrong = 0
    for m in matches:
        item = case.expected_items[m.expected_idx]
        if item.seller_price is None:
            continue
        pv = get_seller_price_for_item(prediction, m.predicted_idx)
        if pv is None:
            linking_wrong += 1
            continue
        _, _, price_idx = pv
        pair_ok = any(p[0] == m.predicted_idx and p[1] == price_idx
                      for p in prediction.item_to_price_pairs)
        if pair_ok:
            linking_ok += 1
        else:
            linking_wrong += 1

    # image kind（raw_merge 才有；僅 vision_raw 傳入）
    image_kind_correct: bool | None = None
    if raw_merge is not None and raw_merge.image_evidence:
        ev_kinds = {ev.image_index: ev.image_kind.value
                    for ev in raw_merge.image_evidence}
        correct = 0
        total = 0
        for im in case.images:
            if im.image_index in ev_kinds:
                total += 1
                expected_kind = _EXPECTED_TO_VISION_KIND.get(
                    im.image_kind.value, im.image_kind.value)
                if ev_kinds[im.image_index] == expected_kind:
                    correct += 1
        if total:
            image_kind_correct = correct == total

    # conflict（raw_merge 提供 raw conflicts；production 用 prediction.conflicts）
    conflict_expected = any("conflict" in (it.notes or "").lower()
                            for it in case.expected_items) or \
        any("conflict" in tag.lower() for tag in case.tags)
    conflict_detected = bool(prediction.conflicts) or bool(
        raw_merge is not None and raw_merge.conflicts)

    notes = []
    if prediction.fallback_used and prediction.fallback_used != "none":
        notes.append(f"fallback={prediction.fallback_used}")

    return CaseEvaluationResult(
        case_id=case.case_id, parser_name=parser_name,
        expected_safe=expected_safe,
        predicted_safe=is_prediction_safe(prediction),
        image_kind_correct=image_kind_correct,
        item_exact_matches=exact, item_partial_matches=partial,
        item_false_positives=len(predicted_unmatched),
        item_false_negatives=len(expected_unmatched),
        seller_price_exact_matches=pr.correct_seller_ask,
        seller_price_missed=pr.missed_seller_ask,
        seller_price_wrong_amount=pr.wrong_amount,
        seller_price_wrong_currency=pr.wrong_currency,
        false_seller_asks=pr.false_seller_asks,
        reference_promoted_to_seller=pr.reference_promoted_to_seller,
        seller_ask_on_wrong_item=pr.seller_ask_on_wrong_item,
        seller_negative_item_false_positives=pr.seller_negative_item_false_positives,
        extra_unmatched_seller_asks=pr.extra_unmatched_seller_asks,
        seller_price_negative_opportunities=pr.negative_opportunities,
        currency_exact_matches=cur_ok, currency_wrong=cur_wrong,
        wear_exact_matches=wear_ok, wear_wrong=wear_wrong,
        linking_correct=linking_ok, linking_wrong=linking_wrong,
        conflict_expected=conflict_expected, conflict_detected=conflict_detected,
        fallback_used=prediction.fallback_used,
        latency_ms=prediction.latency_ms, notes=notes,
    )
