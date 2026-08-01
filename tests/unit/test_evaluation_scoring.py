"""test_evaluation_scoring.py — 比對計分測試（Phase 6.4A-6.4B）"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.evaluation.models import ExpectedItem  # noqa: E402
from alkaid_cs2.evaluation.prediction import EvaluationPrediction  # noqa: E402
from alkaid_cs2.evaluation.scoring import (  # noqa: E402
    MetricCounts, evaluate_safe_decision, get_seller_price_for_item,
    is_prediction_safe, match_expected_items, match_seller_prices,
    score_case,
)


def exp_item(mhn="AK-47 | Redline (Field-Tested)", weapon="AK-47", skin="Redline",
             wear="Field-Tested", stattrak=False, price=Decimal("5000"),
             currency=Currency.TWD):
    return ExpectedItem(market_hash_name=mhn, weapon=weapon, skin=skin,
                        wear=wear, stattrak=stattrak, role="selling",
                        seller_price=price, currency=currency, image_indexes=[0])


def pred(mhns=("AK-47 | Redline (Field-Tested)",), wears=("Field-Tested",),
         prices=(Decimal("5000"),), curs=(Currency.TWD,), idxs=(0,),
         stattrak_values=None):
    n_items = len(mhns)
    wears = list(wears) + [""] * (n_items - len(wears))
    sts = list(stattrak_values) if stattrak_values is not None else [None] * n_items
    n = len(prices)
    return EvaluationPrediction(
        case_id="c1", parser_name="vision_v2",
        market_hash_names=list(mhns), wear_values=list(wears),
        stattrak_values=list(sts),
        seller_prices=list(prices), seller_price_item_indexes=list(idxs),
        currencies=list(curs),
        price_types=["seller_ask"] * n,
        price_indexes=list(range(n)) if n else [],
        item_to_price_pairs=list(zip(idxs, range(n))) if n else [],
    )


def test_metric_counts_zero_safe():
    m = MetricCounts()
    assert m.precision() == 0.0
    assert m.recall() == 0.0
    assert m.f1() == 0.0
    assert m.accuracy() == 0.0


def test_exact_name_match():
    m, _, _ = match_expected_items([exp_item()], pred())
    assert len(m) == 1 and m[0].level == "exact"


def test_component_exact_match():
    m, _, _ = match_expected_items(
        [exp_item()],
        pred(mhns=("AK-47 | Redline (Field-Tested)",), wears=("Field-Tested",)))
    assert len(m) == 1 and m[0].level == "exact"


def test_missing_wear_partial():
    m, _, _ = match_expected_items(
        [exp_item()],
        pred(mhns=("AK-47 | Redline (Factory New)",), wears=("Factory New",)))
    # wear 不同 → 不 exact（可能 partial？weapon+skin 同但 wear 明確不同 → 0）
    assert len(m) == 0, "wear 明確不同不匹配"


def test_wear_conflict_not_exact():
    m, _, _ = match_expected_items(
        [exp_item(mhn="AK-47 | Redline (Factory New)", wear="Factory New")],
        pred(mhns=("AK-47 | Redline (Field-Tested)",), wears=("Field-Tested",)))
    assert len(m) == 0


def test_stattrak_conflict():
    m, _, _ = match_expected_items(
        [exp_item(stattrak=True)],
        pred(mhns=("StatTrak™ AK-47 | Redline (Field-Tested)",),
             stattrak_values=(False,)))  # mhn 有前綴但 stattrak 欄位明確 False
    assert len(m) == 0, "stattrak 不同不 exact"


def test_skin_only_not_exact():
    # predicted 只有 skin（無 weapon/wear）
    m, _, _ = match_expected_items(
        [exp_item()],
        pred(mhns=("Redline",), wears=("",)))
    assert len(m) == 0, "skin-only 不得 exact"


def test_predicted_extra_item():
    e = [exp_item()]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "AWP | Dragon Lore (Factory New)"))
    m, exp_un, pred_un = match_expected_items(e, p)
    assert len(m) == 1
    assert pred_un == [1], "多出的 item"


def test_predicted_missing_item():
    e = [exp_item(), exp_item(mhn="AWP | Dragon Lore (Factory New)",
                              weapon="AWP", skin="Dragon Lore")]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",))
    m, exp_un, _ = match_expected_items(e, p)
    assert exp_un == [1], "遺漏的 item"


def test_exact_seller_price():
    pr = match_seller_prices([exp_item()], pred(),
                             match_expected_items([exp_item()], pred())[0])
    assert pr.correct_seller_ask == 1


def test_wrong_currency():
    p = pred(curs=(Currency.RMB,))
    pr = match_seller_prices([exp_item()], p,
                             match_expected_items([exp_item()], p)[0])
    assert pr.wrong_currency == 1
    assert pr.correct_seller_ask == 0


def test_reference_not_seller():
    # expected 無 seller_price（reference 圖）；predicted 也無 seller price
    e = [exp_item(price=None, currency=None)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",), prices=(), curs=(), idxs=())
    pr = match_seller_prices(e, p, match_expected_items(e, p)[0])
    assert pr.reference_promoted_to_seller == 0


def test_false_seller_price():
    # expected 無 seller（buying）；predicted 有 seller price
    e = [exp_item(price=None, currency=None)]
    p = pred(prices=(Decimal("5000"),), curs=(Currency.TWD,), idxs=(0,))
    pr = match_seller_prices(e, p, match_expected_items(e, p)[0])
    assert pr.false_seller_asks == 1 or pr.reference_promoted_to_seller == 1


def test_linking_correct():
    p = pred()
    p.linked_pairs = [(0, 0)]
    # 評估 linking 由 score_case 負責；此處驗證 linked 資料保留
    assert p.linked_pairs == [(0, 0)]


def test_linking_wrong():
    p = pred()
    assert p.linked_pairs == [], "無 linking"


def test_safe_true_positive():
    d = evaluate_safe_decision(True, pred())
    assert d["safe_true_positive"] == 1
    assert d["safe_false_positive"] == 0


def test_safe_false_positive():
    # expected 不安全但 prediction safe（blocked=False）
    d = evaluate_safe_decision(False, pred())
    assert d["safe_false_positive"] == 1, "最重要指標：系統誤放行"


def test_safe_false_negative():
    p = pred()
    p.blocked = True
    d = evaluate_safe_decision(True, p)
    assert d["safe_false_negative"] == 1


def test_safe_true_negative():
    p = pred()
    p.blocked = True
    d = evaluate_safe_decision(False, p)
    assert d["safe_true_negative"] == 1


def test_deterministic_matching():
    e = [exp_item(), exp_item(mhn="AWP | Dragon Lore (Factory New)",
                              weapon="AWP", skin="Dragon Lore")]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",
                   "AWP | Dragon Lore (Factory New)"))
    m1, _, _ = match_expected_items(e, p)
    m2, _, _ = match_expected_items(e, p)
    assert [(x.expected_idx, x.predicted_idx) for x in m1] == \
        [(x.expected_idx, x.predicted_idx) for x in m2], "deterministic"


# ================================================================
# Phase 6.4B.1 — Metric Correctness
# ================================================================
def test_stattrak_exact_match():
    e = [exp_item(mhn="StatTrak™ AK-47 | Redline (Field-Tested)",
                  stattrak=True)]
    p = pred(mhns=("StatTrak™ AK-47 | Redline (Field-Tested)",))
    m, _, _ = match_expected_items(e, p)
    assert len(m) == 1 and m[0].level == "exact", \
        "StatTrak™ 前綴解析後 exact"


def test_stattrak_conflict_not_match():
    e = [exp_item(mhn="StatTrak™ AK-47 | Redline (Field-Tested)",
                  stattrak=True)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",))  # 非 StatTrak
    m, _, _ = match_expected_items(e, p)
    assert len(m) == 0, "stattrak 衝突不 exact"


def test_currency_uses_seller_price_item_mapping():
    # 2 items：A 無價格、B 有價格 → currency 必須經 seller_price_item_indexes
    p = EvaluationPrediction(
        case_id="c1", parser_name="vision_raw",
        market_hash_names=["AK-47 | Redline (Field-Tested)",
                           "AWP | Dragon Lore (Factory New)"],
        wear_values=["Field-Tested", "Factory New"],
        seller_prices=[Decimal("14000")],
        seller_price_item_indexes=[1],  # B 的價格
        currencies=[Currency.TWD],
        price_types=["seller_ask"],
        price_indexes=[1],
        item_to_price_pairs=[(1, 1)])
    # B 的 currency 走 index 1 → TWD
    assert get_seller_price_for_item(p, 1) == (Decimal("14000"), Currency.TWD, 1)
    assert get_seller_price_for_item(p, 0) is None, "A 無價格"


def test_item_without_price_does_not_shift_currency():
    p = EvaluationPrediction(
        case_id="c1", parser_name="vision_raw",
        market_hash_names=["A", "B"], wear_values=["", ""],
        seller_prices=[Decimal("5000")], seller_price_item_indexes=[0],
        currencies=[Currency.TWD], price_types=["seller_ask"],
        price_indexes=[0], item_to_price_pairs=[(0, 0)])
    assert get_seller_price_for_item(p, 0) == (Decimal("5000"), Currency.TWD, 0)
    assert get_seller_price_for_item(p, 1) is None


def test_linking_item_price_indexes_correct():
    p = pred()
    p.price_indexes = [0]
    p.item_to_price_pairs = [(0, 0)]
    # linking 正確：price 的 associated item == predicted item
    assert (0, 0) in p.item_to_price_pairs


def test_wrong_item_link_detected():
    p = pred()
    p.price_indexes = [2]  # price 屬於 item 2
    p.item_to_price_pairs = [(0, 2)]  # 但關聯到 item 0
    from alkaid_cs2.evaluation.scoring import get_seller_price_for_item
    pv = get_seller_price_for_item(p, 0)
    assert pv is not None
    _, _, price_idx = pv
    assert price_idx == 2


def test_seller_fp_not_based_on_item_fp():
    # item FP=1 但 seller FP 必須獨立計算（0）
    e = [exp_item()]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "Extra Item"))
    pr = match_seller_prices(e, p, match_expected_items(e, p)[0])
    assert pr.false_seller_asks == 0, "item FP 不應直接變 seller FP"


def test_reference_promoted_counted():
    e = [exp_item(price=None, currency=None)]  # expected 無 seller（reference）
    p = pred(prices=(Decimal("5000"),), curs=(Currency.TWD,), idxs=(0,))
    pr = match_seller_prices(e, p, match_expected_items(e, p)[0])
    assert pr.reference_promoted_to_seller == 1, "reference 被提升計數"


def test_item_match_recall_includes_partial():
    # partial match 進 recall（exact+partial）/ expected
    e = [exp_item()]
    p = pred(mhns=("AK-47 | Redline",), wears=("",))  # wear 缺失 → partial
    m, exp_un, _ = match_expected_items(e, p)
    assert len(m) == 1 and m[0].level == "partial"
    total = len(e)
    recall = (sum(1 for x in m if x.level == "exact") +
              sum(1 for x in m if x.level == "partial")) / total
    assert recall == 1.0, "partial 計入 recall"


def test_raw_safe_matrix_separate():
    # expected_safe=None（raw 無標註）→ 不計入 matrix
    d = evaluate_safe_decision(None, pred())
    assert d["excluded"] is True
    assert d["safe_true_positive"] == 0 and d["safe_false_positive"] == 0


# ================================================================
# Phase 6.4B.2 — Denominator & Index Finalization
# ================================================================
def test_unnamed_item_before_named_does_not_shift_price_mapping():
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    # source item 0 無名稱、item 1 有名稱與價格
    items = [
        {"market_hash_name": None, "wear": "", "role": "unknown",
         "stattrak": None, "seller_price": None, "currency": None,
         "price_idx": None, "linked_price_indexes": []},
        {"market_hash_name": "AK-47 | Redline (Field-Tested)",
         "wear": "Field-Tested", "role": "selling", "stattrak": False,
         "seller_price": "5000", "currency": "TWD", "price_idx": 1,
         "linked_price_indexes": [1]},
    ]
    p = _prediction_from_items(case, "vision_raw", items)
    assert len(p.market_hash_names) == 1
    assert p.seller_price_item_indexes == [0], "壓縮後 index=0"
    assert p.item_to_price_pairs == [(0, 1)], "壓縮後 item index=0"
    assert get_seller_price_for_item(p, 0) == (Decimal("5000"), Currency.TWD, 1)


def test_skipped_item_does_not_break_currency_mapping():
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    items = [
        {"market_hash_name": "AWP | Dragon Lore (Factory New)",
         "wear": "Factory New", "role": "unknown", "stattrak": False,
         "seller_price": None, "currency": None, "price_idx": None,
         "linked_price_indexes": []},
        {"market_hash_name": None, "wear": "", "role": "unknown",
         "stattrak": None, "seller_price": None, "currency": None,
         "price_idx": None, "linked_price_indexes": []},
        {"market_hash_name": "AK-47 | Redline (Field-Tested)",
         "wear": "Field-Tested", "role": "selling", "stattrak": False,
         "seller_price": "5000", "currency": "TWD", "price_idx": 2,
         "linked_price_indexes": [2]},
    ]
    p = _prediction_from_items(case, "vision_raw", items)
    assert p.seller_price_item_indexes == [1], "跳過無名 item 後壓縮 index=1"
    assert p.currencies == [Currency.TWD]
    assert get_seller_price_for_item(p, 1) == (Decimal("5000"), Currency.TWD, 2)
    assert get_seller_price_for_item(p, 0) is None


def test_skipped_item_does_not_break_linking():
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    items = [
        {"market_hash_name": None, "wear": "", "role": "unknown",
         "stattrak": None, "seller_price": None, "currency": None,
         "price_idx": None, "linked_price_indexes": []},
        {"market_hash_name": "AK-47 | Redline (Field-Tested)",
         "wear": "Field-Tested", "role": "selling", "stattrak": False,
         "seller_price": "5000", "currency": "TWD", "price_idx": 1,
         "linked_price_indexes": [1]},
    ]
    p = _prediction_from_items(case, "vision_raw", items)
    assert p.item_to_price_pairs == [(0, 1)]
    # linking：seller price 的 associated item（0）== matched predicted item（0）
    ok = any(pair[0] == 0 and pair[1] == 1 for pair in p.item_to_price_pairs)
    assert ok, "壓縮後 linking 仍正確"


def test_missing_currency_remains_none():
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    items = [{"market_hash_name": "A", "wear": "", "role": "selling",
              "stattrak": False, "seller_price": "5000", "currency": None,
              "price_idx": 0, "linked_price_indexes": [0]}]
    p = _prediction_from_items(case, "vision_raw", items)
    assert p.currencies == [None], "缺失 currency 保存 None（不默認 TWD）"


def test_invalid_currency_not_coerced_to_twd():
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    items = [{"market_hash_name": "A", "wear": "", "role": "selling",
              "stattrak": False, "seller_price": "5000", "currency": "XYZ",
              "price_idx": 0, "linked_price_indexes": [0]}]
    p = _prediction_from_items(case, "vision_raw", items)
    assert p.currencies == [None], "未知 currency 不強轉 TWD"


def test_unknown_currency_counts_wrong_or_unresolved():
    # currency None → 與 expected TWD 比對 → wrong（不 exact）
    from alkaid_cs2.evaluation.evaluator import _prediction_from_items
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="t")
    items = [{"market_hash_name": "AK-47 | Redline (Field-Tested)",
              "wear": "Field-Tested", "role": "selling", "stattrak": False,
              "seller_price": "5000", "currency": None,
              "price_idx": 0, "linked_price_indexes": [0]}]
    p = _prediction_from_items(case, "vision_raw", items)
    m = match_expected_items([exp_item()], p)[0]
    pr = match_seller_prices([exp_item()], p, m)
    assert pr.correct_seller_ask == 0, "currency None 不算 exact"
    assert pr.wrong_currency == 1 or pr.missed_seller_ask == 1


def test_stattrak_component_parsing():
    from alkaid_cs2.evaluation.scoring import _split_mhn
    assert _split_mhn("StatTrak™ AK-47 | Redline") == ("AK-47", "Redline", True)
    assert _split_mhn("★ StatTrak™ Karambit | Doppler") == \
        ("Karambit", "Doppler", True)
    assert _split_mhn("★ Karambit | Doppler") == ("Karambit", "Doppler", False)
    assert _split_mhn("★ Karambit | Doppler (Factory New)") == \
        ("Karambit", "Doppler", False)


def test_stattrak_component_only_exact():
    # component-only 匹配（非完整 mhn exact）：StatTrak Karambit
    e = [ExpectedItem(weapon="Karambit", skin="Doppler", wear="Factory New",
                      stattrak=True, role="selling", image_indexes=[0])]
    p = pred(mhns=("★ StatTrak™ Karambit | Doppler (Factory New)",),
             wears=("Factory New",), stattrak_values=(True,))
    m, _, _ = match_expected_items(e, p)
    assert len(m) == 1 and m[0].level == "exact", "component-only exact"


# ================================================================
# Phase 6.4B.3 — Seller price 語意
# ================================================================
def test_seller_positive_unmatched_item_is_wrong_item_not_false_positive():
    # GT 有 seller ask；predicted 多出一個 unmatched item 帶 ask → wrong_item（非 false）
    e = [exp_item(price=Decimal("5000"), currency=Currency.TWD)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "Extra | Item"),
             prices=(Decimal("5000"), Decimal("9999")),
             curs=(Currency.TWD, Currency.TWD), idxs=(0, 1))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.correct_seller_ask == 1
    assert pr.seller_ask_on_wrong_item == 1, "unmatched ask → wrong_item"
    assert pr.false_seller_asks == 0, "不得計入 false_seller_asks"


def test_seller_negative_extra_ask_is_false_positive():
    # GT 全無 seller ask；predicted 多出 unmatched ask → false_seller_asks
    e = [exp_item(price=None, currency=None)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "Extra | Item"),
             prices=(Decimal("9999"),), curs=(Currency.TWD,), idxs=(1,))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.false_seller_asks == 1, "無 seller 語境 → false positive"
    assert pr.seller_ask_on_wrong_item == 0
    assert pr.negative_opportunities == 1


def test_reference_promoted_is_false_positive():
    # matched item 的 ask 被提升（GT 無 seller）→ reference_promoted
    e = [exp_item(price=None, currency=None)]
    p = pred(prices=(Decimal("5000"),), curs=(Currency.TWD,), idxs=(0,))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.reference_promoted_to_seller == 1, "reference 被提升 → false 類"
    assert pr.false_total == 1
    assert pr.seller_ask_on_wrong_item == 0


def test_seller_fp_rate_never_exceeds_one():
    # false numerator ≤ negative opportunities → rate ∈ [0,1]
    from alkaid_cs2.evaluation.report import _pct
    for num, den in ((0, 2), (1, 2), (2, 2), (3, 0)):
        rate = _pct(num, den)
        assert 0.0 <= rate <= 1.0, f"rate {rate} 超出範圍"


def test_wrong_item_count_reported_separately():
    from alkaid_cs2.evaluation.scoring import PriceMatchResult
    r = PriceMatchResult(correct_seller_ask=1, seller_ask_on_wrong_item=2,
                         negative_opportunities=3)
    assert r.false_total == 0, "wrong_item 不進 false_total"


# ================================================================
# Phase 6.4B.4 — Negative Opportunity 統計單位 & Safe 一致性
# ================================================================
def _pred(**kw):
    base = dict(case_id="c1", parser_name="vision_production",
                parse_status="parsed", source="v2", blocked=False)
    base.update(kw)
    return EvaluationPrediction(**base)


def test_parsed_not_blocked_is_safe():
    assert is_prediction_safe(_pred()) is True


def test_blocked_parsed_is_not_safe():
    assert is_prediction_safe(_pred(blocked=True)) is False


def test_skipped_source_is_not_safe():
    assert is_prediction_safe(_pred(source="skipped")) is False


def test_unresolved_not_blocked_is_not_safe():
    assert is_prediction_safe(_pred(parse_status="unresolved")) is False


def test_error_not_blocked_is_not_safe():
    assert is_prediction_safe(_pred(parse_status="error")) is False


def test_score_case_and_evaluate_safe_decision_agree():
    # 同一 prediction：score_case.predicted_safe == evaluate_safe_decision
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    case = EvaluationCase(case_id="c1", source=EvaluationSource.SYNTHETIC,
                          author="a", link="l", raw_text="售 A 算5000")
    for kw in (dict(blocked=True), dict(source="skipped"),
               dict(parse_status="unresolved"), dict(parse_status="error"),
               dict()):
        p = _pred(**kw)
        sc = score_case(case, "vision_production", p,
                        expected_safe=True)
        dec = evaluate_safe_decision(True, p)
        assert sc.predicted_safe == dec["predicted_safe"], \
            f"{kw} 兩處判定不一致"


def test_safe_matrix_does_not_accept_unresolved():
    d = evaluate_safe_decision(False, _pred(parse_status="unresolved"))
    assert d["safe_false_positive"] == 0, "unresolved 不得 safe FP"


def test_safe_matrix_does_not_accept_error():
    d = evaluate_safe_decision(False, _pred(parse_status="error"))
    assert d["safe_false_positive"] == 0, "error 不得 safe FP"


def test_one_negative_item_three_extra_asks_rate_not_over_one():
    # GT：1 個無 seller 的 item（matched）+ predicted 3 個額外 unmatched asks
    # 正式 FPR = negative_item_fp / neg_opps → 不得 3/1=3.0
    e = [exp_item(price=None, currency=None)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "X1 | A", "X2 | B", "X3 | C"),
             prices=(Decimal("9999"), Decimal("1"), Decimal("2"), Decimal("3")),
             curs=(Currency.TWD,) * 4, idxs=(0, 1, 2, 3))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    # idx 0 matched（GT 無 seller）→ negative_item_fp；其餘 unmatched → extra
    assert pr.seller_negative_item_false_positives == 1
    assert pr.extra_unmatched_seller_asks == 3
    from alkaid_cs2.evaluation.report import _pct
    rate = _pct(pr.seller_negative_item_false_positives, pr.negative_opportunities)
    assert rate == 1.0, f"FPR 不得超過 1：{rate}"
    assert 0.0 <= rate <= 1.0


def test_matched_negative_item_with_ask_is_one_fp():
    e = [exp_item(price=None, currency=None)]
    p = pred(prices=(Decimal("5000"),), curs=(Currency.TWD,), idxs=(0,))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.seller_negative_item_false_positives == 1
    assert pr.false_total == 1
    assert pr.extra_unmatched_seller_asks == 0


def test_multiple_extra_unmatched_asks_reported_separately():
    e = [exp_item(price=Decimal("5000"), currency=Currency.TWD)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "X1 | A", "X2 | B"),
             prices=(Decimal("5000"), Decimal("1"), Decimal("2")),
             curs=(Currency.TWD,) * 3, idxs=(0, 1, 2))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.correct_seller_ask == 1
    assert pr.seller_ask_on_wrong_item == 2, "GT 有 ask → wrong item"
    assert pr.extra_unmatched_seller_asks == 0, "GT 有 ask 不進 extra"
    assert pr.seller_negative_item_false_positives == 0


def test_wrong_item_ask_not_negative_item_fp():
    e = [exp_item(price=Decimal("5000"), currency=Currency.TWD)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)", "Extra | Item"),
             prices=(Decimal("5000"), Decimal("9999")),
             curs=(Currency.TWD, Currency.TWD), idxs=(0, 1))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.seller_ask_on_wrong_item == 1
    assert pr.seller_negative_item_false_positives == 0


def test_seller_negative_fp_denominator_zero_safe():
    # denominator=0：FPR 輸出 0.0、denominator 欄位保留 0
    from alkaid_cs2.evaluation.report import _pct
    assert _pct(0, 0) == 0.0
    assert _pct(1, 0) == 0.0  # 不 crash


# ================================================================
# Phase 6.4B.5 — Negative Item 去重
# ================================================================
def test_same_negative_item_multiple_asks_counts_one_fp():
    # GT：1 個無 seller item；predicted 同一 item 掛 3 筆 ask → 只計 1 FP
    e = [exp_item(price=None, currency=None)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",),
             prices=(Decimal("5000"), Decimal("5100"), Decimal("5200")),
             curs=(Currency.TWD, Currency.TWD, Currency.TWD),
             idxs=(0, 0, 0))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.seller_negative_item_false_positives == 1, "去重後 1 FP"
    assert pr.negative_opportunities == 1
    assert pr.reference_promoted_to_seller == 1, "alias 用去重後數值"
    from alkaid_cs2.evaluation.report import _pct
    assert _pct(pr.seller_negative_item_false_positives,
                pr.negative_opportunities) == 1.0
    assert pr.seller_negative_item_false_positives <= pr.negative_opportunities


def test_two_negative_items_multiple_asks_count_per_item():
    # GT：2 個無 seller items；item0 掛 2 筆、item1 掛 3 筆 → 每 item 1 FP
    e = [exp_item(mhn="AK-47 | Redline (Field-Tested)", price=None,
                  currency=None),
         exp_item(mhn="AWP | Dragon Lore (Factory New)", price=None,
                  currency=None)]
    p = pred(mhns=("AK-47 | Redline (Field-Tested)",
                   "AWP | Dragon Lore (Factory New)"),
             wears=("Field-Tested", "Factory New"),
             prices=(Decimal("1"), Decimal("2"), Decimal("3"),
                     Decimal("4"), Decimal("5")),
             curs=(Currency.TWD,) * 5,
             idxs=(0, 0, 1, 1, 1))
    m = match_expected_items(e, p)[0]
    pr = match_seller_prices(e, p, m)
    assert pr.seller_negative_item_false_positives == 2, "每 negative item 1 FP"
    assert pr.negative_opportunities == 2
    from alkaid_cs2.evaluation.report import _pct
    assert _pct(pr.seller_negative_item_false_positives,
                pr.negative_opportunities) == 1.0
    assert pr.seller_negative_item_false_positives <= pr.negative_opportunities


def test_negative_item_fp_never_exceeds_opportunities():
    # 窮舉：多筆 ask 掛在 matched negative items → FP ≤ opportunities
    e = [exp_item(price=None, currency=None)]
    for n_asks in (1, 3, 10):
        p = pred(mhns=("AK-47 | Redline (Field-Tested)",),
                 prices=tuple(Decimal(str(i)) for i in range(1, n_asks + 1)),
                 curs=(Currency.TWD,) * n_asks,
                 idxs=(0,) * n_asks)
        m = match_expected_items(e, p)[0]
        pr = match_seller_prices(e, p, m)
        assert pr.seller_negative_item_false_positives <= \
            pr.negative_opportunities, f"{n_asks} 筆 ask 不得超過 1 FP"


def test_duplicate_asks_do_not_change_readiness_fpr_above_one():
    # report 正式 FPR 不因同商品多筆 ask 超過 1
    from alkaid_cs2.evaluation.report import _pct
    from alkaid_cs2.evaluation.scoring import PriceMatchResult
    for n_asks in (1, 3, 10):
        pr = PriceMatchResult(
            seller_negative_item_false_positives=1,
            extra_unmatched_seller_asks=n_asks - 1,
            negative_opportunities=1)
        rate = _pct(pr.seller_negative_item_false_positives,
                    pr.negative_opportunities)
        assert 0.0 <= rate <= 1.0, f"{n_asks} 筆 ask FPR 超過 1"
