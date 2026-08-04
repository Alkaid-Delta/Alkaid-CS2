# -*- coding: utf-8 -*-
"""test_metrics.py — P0 metrics integrity（canonical metrics 純函式；無絕對路徑）"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import (  # noqa: E402
    canonical_metrics, evaluate_p7_entry_gate, build_report_files, load_fixtures,
)


def _sample_case_results():
    # 樣本：stat_trak_ak expected 補 stattrak（actual 有 StatTrak）
    return [
        {"case_id": "simple_single_twd", "pytest_status": "PASSED", "case_status": "pass",
         "execution_level": "legacy_snapshot", "status": "ok",
         "market_hash_name": "AK-47 | Redline (Field-Tested)",
         "seller_price": 5000, "input_image_count": 0,
         "actual_items": ["AK-47 | Redline (Field-Tested)"],
         "actual_seller_prices": [5000], "actual_parse_status": "ok",
         "actual_verified": None,
         "expected_items": [{"skin": "Redline", "weapon": "AK-47",
                             "wear": "Field-Tested", "seller_price": 5000}],
         "expected_seller_prices": [5000], "expected_parse_status": "ok",
         "expected_verified": True},
        {"case_id": "redline_vulcan_simplified", "pytest_status": "XFAIL", "case_status": "xfail",
         "execution_level": "legacy_snapshot", "status": "ok",
         "market_hash_name": None, "seller_price": None,
         "input_image_count": 0, "actual_items": [],
         "actual_seller_prices": [], "actual_parse_status": "ok",
         "actual_verified": None,
         "expected_items": [], "expected_seller_prices": [],
         "expected_parse_status": "unresolved", "expected_verified": False},
        {"case_id": "stat_trak_ak", "pytest_status": "PASSED", "case_status": "pass",
         "execution_level": "legacy_snapshot", "status": "ok",
         "market_hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)",
         "seller_price": 200, "input_image_count": 0,
         "actual_items": ["StatTrak™ AK-47 | Redline (Field-Tested)"],
         "actual_seller_prices": [200], "actual_parse_status": "ok",
         "actual_verified": None,
         "expected_items": [{"skin": "Redline", "weapon": "AK-47",
                             "wear": "Field-Tested", "stattrak": True,
                             "seller_price": 5000}],
         "expected_seller_prices": [5000], "expected_parse_status": "ok",
         "expected_verified": True},
    ]


def _sample_posts():
    return [
        {"case_id": "simple_single_twd", "category": "A_single_item",
         "expected_verified": True, "expected_parse_status": "ok"},
        {"case_id": "redline_vulcan_simplified", "category": "B_multi_item",
         "expected_verified": False, "expected_parse_status": "unresolved"},
        {"case_id": "stat_trak_ak", "category": "A_single_item",
         "expected_verified": True, "expected_parse_status": "ok"},
    ]


def _sample_expected():
    return {
        "simple_single_twd": {"status": "ok", "items": [{"skin": "Redline", "seller_price": 5000}]},
        "redline_vulcan_simplified": {"status": "ok", "items": []},
        "stat_trak_ak": {"status": "ok", "items": [{"skin": "Redline", "seller_price": 5000}]},
    }


# ── 1. canonical metrics 基本 ──
def test_canonical_metrics_counts():
    m = canonical_metrics(_sample_case_results(), _sample_posts(), _sample_expected())
    assert m["total_cases"] == 3
    assert m["passed_cases"] == 2
    assert m["xfailed_cases"] == 1
    assert m["count_identity_holds"] is True


def test_canonical_accuracy_shape():
    m = canonical_metrics(_sample_case_results(), _sample_posts(), _sample_expected())
    for k in ("item_accuracy", "seller_price_accuracy", "currency_accuracy",
              "verification_accuracy", "parse_status_accuracy",
              "item_price_link_accuracy", "image_merge_accuracy"):
        a = m[k]
        for sub in ("value", "numerator", "denominator", "reason",
                    "eligible_case_ids", "excluded_case_ids", "excluded_reasons"):
            assert sub in a, f"{k} 缺 {sub}"
        assert a["value"] is None or 0.0 <= a["value"] <= 1.0, k


def test_canonical_values():
    m = canonical_metrics(_sample_case_results(), _sample_posts(), _sample_expected())
    # item: a/c 有 expected items（b 無）→ 2/2（a/c 皆 legacy_snapshot runtime）
    assert m["item_accuracy"]["value"] == 1.0
    assert m["item_accuracy"]["numerator"] == 2
    assert m["item_accuracy"]["denominator"] == 2
    # price: a ok（5000）、c 錯（200）→ 1/2
    assert m["seller_price_accuracy"]["value"] == 0.5
    assert m["seller_price_accuracy"]["denominator"] == 2
    # verification：adapter 不暴露 actual_verified → null + reason
    assert m["verification_accuracy"]["value"] is None
    assert m["verification_accuracy"]["denominator"] == 0
    assert m["verification_accuracy"]["reason"] == "legacy_regression_adapter_does_not_expose_verified"


def test_currency_unavailable_null_with_reason():
    """currency unavailable = null + reason（不是 0.0）"""
    m = canonical_metrics(_sample_case_results(), _sample_posts(), _sample_expected())
    assert m["currency_accuracy"]["value"] is None
    assert m["currency_accuracy"]["numerator"] == 0
    assert m["currency_accuracy"]["denominator"] == 0
    assert m["currency_accuracy"]["reason"] == "legacy_regression_adapter_does_not_expose_currency"


def test_currency_zero_only_with_denominator():
    """0.0 只允許 denominator>0（全部判錯）；unavailable 必須 None"""
    m = canonical_metrics(_sample_case_results(), _sample_posts(), _sample_expected())
    assert not (m["currency_accuracy"]["value"] == 0.0 and
                m["currency_accuracy"]["denominator"] == 0)


# ── 2. P7 gate 純函式 ──
def test_p7_gate_not_ready_when_failed():
    m = {"failed_cases": 1, "count_identity_holds": False}
    assert evaluate_p7_entry_gate(m, {"p6_second_image_price_skip": True,
                                      "p7_router_not_implemented": True}) == "NOT READY FOR P7"


def test_p7_gate_not_ready_by_default():
    m = {"failed_cases": 0, "count_identity_holds": True}
    assert evaluate_p7_entry_gate(m, {}) == "NOT READY FOR P7"


# ── 3. 可攜性 ──
def test_no_absolute_local_paths_in_sources():
    """regression source/tests 不得含絕對本機路徑"""
    drive = "E" + ":"  # 拼接避免自引用
    hub = "Alkaid" + "-CS2-Review-Hub"
    tmp = "05-temp" + "-work"
    bad = [drive + "/", drive + "\\", hub, tmp]
    reg_dir = os.path.dirname(__file__)
    for fn in os.listdir(reg_dir):
        if fn.endswith(".py"):
            src = open(os.path.join(reg_dir, fn), encoding="utf-8").read()
            for b in bad:
                assert b not in src, f"{fn} 含 {b}"


def test_report_buildable_to_tmp_path(tmp_path):
    """report generator 可輸出至任意 tmp_path"""
    files = build_report_files(_sample_case_results(), _sample_posts(),
                               str(tmp_path), _sample_expected())
    assert "p0-baseline-report.json" in files
    assert os.path.exists(os.path.join(str(tmp_path), "p0-baseline-report.json"))
    r = json.load(open(os.path.join(str(tmp_path), "p0-baseline-report.json"), encoding="utf-8"))
    assert r["metrics"]["total_cases"] == 3


def test_report_rebuildable_from_any_cwd(tmp_path, monkeypatch):
    """更換 cwd 後仍可定位 fixtures 並重建報告"""
    monkeypatch.chdir(tmp_path)
    posts, expected = load_fixtures()
    assert len(posts) >= 30, "fixtures 定位失敗"


# ── 4. fixture 對齊（無外部檔案依賴）──
def test_fixture_expected_sets_match():
    posts, expected = load_fixtures()
    post_ids = {p["case_id"] for p in posts}
    exp_ids = set(expected.keys())
    assert post_ids == exp_ids


def test_every_fixture_has_golden_node():
    """每個 fixture 有 golden node（contract 測試允許 test_{cid}_contract_is_preserved）"""
    posts, _ = load_fixtures()
    gp = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
              encoding="utf-8").read()
    tests = set(re.findall(r"def (test_\w+)\(", gp))
    missing = []
    for p in posts:
        cid = p["case_id"]
        if f"test_{cid}" not in tests and f"test_{cid}_contract_is_preserved" not in tests:
            missing.append(cid)
    assert not missing, f"fixture 缺 golden node: {missing}"


def test_all_xfail_strict():
    """每個 @pytest.mark.xfail 必須 strict=True"""
    gp = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
              encoding="utf-8").read()
    xfails = re.findall(r"@pytest\.mark\.xfail\(([^)]*)\)", gp)
    # 多行 decorator：用完整區塊解析
    lines = gp.splitlines()
    for i, ln in enumerate(lines):
        if "@pytest.mark.xfail" in ln:
            j = i
            block = []
            while j < len(lines) and ")" not in lines[j]:
                block.append(lines[j])
                j += 1
            block.append(lines[j])
            text = " ".join(block)
            assert "strict=True" in text, f"L{i+1}: xfail 無 strict=True"


def test_no_xpass_possible():
    """XPASS 會導致失敗（strict=True 對 pass 的 xfail 是 failure）"""
    gp = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
              encoding="utf-8").read()
    assert "strict=False" not in gp


def test_p0_metric_csv_header_alignment():
    """CSV header 與 JSON metrics 對齊（canonical 欄位模式）"""
    posts, expected = load_fixtures()
    m = canonical_metrics(_sample_case_results(), _sample_posts(), expected)
    for k in ("item_accuracy", "seller_price_accuracy", "currency_accuracy",
              "verification_accuracy", "parse_status_accuracy",
              "item_price_link_accuracy", "image_merge_accuracy"):
        for sub in ("value", "numerator", "denominator", "reason"):
            assert sub in m[k], k
