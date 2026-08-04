# -*- coding: utf-8 -*-
"""test_p0_coverage.py — P0 案例分類覆蓋與 phase 契約驗證"""
import json
import os
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

POSTS = json.load(open(os.path.join(os.path.dirname(__file__),
                                    "fixtures", "posts.json"), encoding="utf-8"))
EXPECTED = json.load(open(os.path.join(os.path.dirname(__file__),
                                       "fixtures", "expected.json"), encoding="utf-8"))


def _cats():
    from collections import Counter
    return Counter(p["category"] for p in POSTS)


def test_category_a_min_4():
    assert _cats()["A_single_item"] >= 4, _cats()


def test_category_b_min_5():
    assert _cats()["B_multi_item"] >= 5, _cats()


def test_category_c_min_6():
    assert _cats()["C_price_link"] >= 6, _cats()


def test_category_d_min_6():
    assert _cats()["D_currency"] >= 6, _cats()


def test_category_e_min_5():
    assert _cats()["E_validation"] >= 5, _cats()


def test_category_f_min_5():
    assert _cats()["F_multi_image"] >= 5, _cats()


def test_category_g_min_4():
    assert _cats()["G_legacy_mode"] >= 4, _cats()


def test_category_h_min_4():
    assert _cats()["H_failure"] >= 4, _cats()


# ── P1/P2 契約 case 必須存在且不得 xfail（由 test_golden_posts 執行）──
def test_p1_currency_cases_present():
    need = ["simple_single_twd", "rmb_price_no_conversion_marker",
            "p0_rmb_single_conversion", "p0_usd_single_conversion",
            "p0_unknown_currency_fail_closed"]
    ids = {p["case_id"] for p in POSTS}
    missing = [n for n in need if n not in ids]
    assert not missing, f"缺 P1 currency case: {missing}"


def test_p2_validation_cases_present():
    need = ["p2_trusted_dict_exact", "p2_alias_canonical",
            "p2_retry_fails_twice", "p2_unknown_model_item",
            "p2_vision_only_unverified", "p2_validator_unavailable",
            "p0_pattern_no_weapon_unverified"]
    ids = {p["case_id"] for p in POSTS}
    missing = [n for n in need if n not in ids]
    assert not missing, f"缺 P2 validation case: {missing}"


def test_p7_p8_future_cases_present():
    ids = {p["case_id"] for p in POSTS}
    assert "p0_p7_flash_default_preview" in ids, "缺 P7 預置"
    assert "p0_p8_llm_profit_override_preview" in ids, "缺 P8 預置"


def test_future_gate_xfail_count():
    """future P7/P8 xfail = 2（strict=True）"""
    import pytest
    # 由 test_golden_posts 的標記決定——這裡驗證 fixture 層存在
    f = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
             encoding="utf-8").read()
    assert f.count("future_gate:") == 2, "future_gate xfail 應為 2"


def test_coverage_matrix_output():
    """coverage matrix CSV 可產生（report 層）"""
    from tests.regression.report import build_coverage_matrix
    rows = build_coverage_matrix()
    assert len(rows) == len(POSTS), f"matrix rows={len(rows)} != posts={len(POSTS)}"
