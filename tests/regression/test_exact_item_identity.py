# -*- coding: utf-8 -*-
"""test_exact_item_identity.py — exact item identity 比較（禁 substring containment）"""
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import (  # noqa: E402
    RegressionItemIdentity, normalize_item_identity, parse_actual_identity,
    identity_matches, load_fixtures, canonical_metrics, evaluate_case,
)


def test_wrong_weapon_same_skin_mismatch():
    """expected AK-47 Redline、actual AWP Redline → 必須判錯"""
    exp = RegressionItemIdentity(weapon="AK-47", skin="Redline", wear="Field-Tested")
    act = parse_actual_identity("AWP | Redline (Field-Tested)")
    assert identity_matches(act, exp) is False


def test_wrong_wear_mismatch():
    exp = RegressionItemIdentity(weapon="AK-47", skin="Redline", wear="Field-Tested")
    act = parse_actual_identity("AK-47 | Redline (Minimal Wear)")
    assert identity_matches(act, exp) is False


def test_missing_stattrak_mismatch():
    exp = RegressionItemIdentity(weapon="AK-47", skin="Redline", stattrak=True)
    act = parse_actual_identity("AK-47 | Redline (Field-Tested)")
    assert identity_matches(act, exp) is False


def test_star_prefix_mismatch():
    exp = RegressionItemIdentity(weapon="Karambit", skin="Tiger Tooth", special_prefix="★")
    act = parse_actual_identity("Karambit | Tiger Tooth (Factory New)")
    assert identity_matches(act, exp) is False


def test_exact_match_ok():
    exp = RegressionItemIdentity(weapon="AK-47", skin="Redline", wear="Field-Tested")
    act = parse_actual_identity("AK-47 | Redline (Field-Tested)")
    assert identity_matches(act, exp) is True


def test_no_substring_containment():
    """AWP | Redline 不得因含 Redline 子字串被判正確"""
    exp = normalize_item_identity({"weapon": "AK-47", "skin": "Redline"})
    assert "Redline" in "AWP | Redline"
    act = parse_actual_identity("AWP | Redline (Field-Tested)")
    assert identity_matches(act, exp) is False


def test_item_metric_unit_and_counts():
    """item metric：unit=item、denominator==eligible_item_count（item-level）"""
    posts, expected = load_fixtures()
    results = [evaluate_case(p, expected.get(p["case_id"])) for p in posts]
    for r in results:
        r["execution_level"] = "legacy_snapshot"
        if r["case_id"] in ("redline_vulcan_simplified", "redline_vulcan_traditional",
                            "seller_ask_plus_buff_floor", "rmb_price_no_conversion_marker",
                            "validation_failure_returns_first", "buying_post_nocts",
                            "trade_only_post", "p0_two_items_diff_price",
                            "p0_same_weapon_diff_skin", "p0_three_items_price",
                            "p0_unlinked_bare_numbers", "p0_stat_trak_star_prefix"):
            r["pytest_status"] = "XFAIL"
        elif r["case_id"] in ("p2_retry_succeeds", "p0_mode_off_legacy",
                              "p0_mode_shadow", "p0_mode_v2_only"):
            r["pytest_status"] = "PASSED"
        else:
            exp = expected.get(r["case_id"], {})
            r["pytest_status"] = "PASSED" if r["status"] == exp.get("status") else "FAILED"
        r["case_status"] = r["pytest_status"]
    m = canonical_metrics(results, posts, expected)
    a = m["item_accuracy"]
    assert a["unit"] == "item"
    assert a["denominator"] == a["eligible_item_count"], "item den != eligible item count"
    assert a["eligible_case_ids"]  # 有 eligible cases


def test_expected_two_actual_one_partial():
    """expected 2 items、actual 1 item → numerator 最多 1"""
    exp = [{"skin": "Redline", "weapon": "AK-47"},
           {"skin": "Vulcan", "weapon": "AK-47"}]
    from tests.regression.report import identity_matches as im
    from tests.regression.report import normalize_item_identity as ni
    from tests.regression.report import parse_actual_identity as pi
    actual = [parse_actual_identity("AK-47 | Redline (Field-Tested)")]
    num = 0
    matched = set()
    for e in exp:
        eid = ni(e)
        for i, a in enumerate(actual):
            if i in matched:
                continue
            if im(a, eid):
                num += 1
                matched.add(i)
                break
    assert num == 1
