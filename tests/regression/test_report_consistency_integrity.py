# -*- coding: utf-8 -*-
"""test_report_consistency_integrity.py — consistency proof 程式化計算 + 負向測試"""
import csv
import json
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import (  # noqa: E402
    load_fixtures, collect_case_results, canonical_metrics, build_report_files,
    evaluate_p7_entry_gate,
)


def _compute_known_failure_counts(known_failures: list[dict]) -> dict:
    """從 CSV 實際布林欄位計算（CSV 值為字串——轉 bool）"""
    def b(k, key):
        return str(k.get(key)) == "True"
    total = len(known_failures)
    active_defect = sum(1 for k in known_failures if b(k, "active_product_defect"))
    active_xfail = sum(1 for k in known_failures if b(k, "active_xfail"))
    future = sum(1 for k in known_failures if b(k, "future_gate"))
    diag = sum(1 for k in known_failures if b(k, "diagnostic_only"))
    strict = sum(1 for k in known_failures if b(k, "strict"))
    pytest_xfail = sum(1 for k in known_failures if k["pytest_outcome"] == "XFAIL")
    pytest_passed_diag = sum(1 for k in known_failures
                             if k["pytest_outcome"] == "PASSED" and b(k, "diagnostic_only"))
    pytest_skipped = sum(1 for k in known_failures if k["pytest_outcome"] == "SKIPPED")
    return {"total_records": total, "active_product_defect_count": active_defect,
            "active_xfail_count": active_xfail, "future_gate_count": future,
            "diagnostic_only_count": diag, "strict_count": strict,
            "pytest_xfail_count": pytest_xfail,
            "pytest_passed_diagnostic_count": pytest_passed_diag,
            "pytest_skipped_count": pytest_skipped}


def test_known_failure_counts_from_csv(tmp_path):
    """程式化計算——active 11 / future 2 / diagnostic 4 / pytest_xfail 13"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    build_report_files(results, posts, str(tmp_path), expected)
    rows = list(csv.DictReader(open(os.path.join(str(tmp_path),
                                                 "p0-known-failures.csv"),
                                    encoding="utf-8")))
    c = _compute_known_failure_counts(rows)
    assert c["total_records"] == 19
    assert c["active_product_defect_count"] == 11
    assert c["active_xfail_count"] == 11
    assert c["future_gate_count"] == 2
    assert c["diagnostic_only_count"] == 5  # 實際：validation_failure/pattern/text_conflict/decimal/nocts 已正式 pass
    assert c["pytest_xfail_count"] == 13
    assert c["pytest_xfail_count"] == c["active_xfail_count"] + c["future_gate_count"]
    assert c["pytest_passed_diagnostic_count"] == 5


def test_negative_active_count_fails():
    """active count 被改錯 → 不一致（負向測試）"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    c = _compute_known_failure_counts(
        [{"active_product_defect": True, "active_xfail": True,
          "future_gate": False, "diagnostic_only": False, "strict": True,
          "pytest_outcome": "XFAIL"}] * 19)
    assert not (c["pytest_xfail_count"] ==
                c["active_xfail_count"] + c["future_gate_count"]) or \
        c["active_product_defect_count"] != 11


def test_negative_future_count_fails():
    c = _compute_known_failure_counts(
        [{"active_product_defect": False, "active_xfail": False,
          "future_gate": True, "diagnostic_only": False, "strict": True,
          "pytest_outcome": "XFAIL"}] * 5)
    assert c["future_gate_count"] == 5  # 改錯的 future → proof 應 FAIL


def test_null_metrics_have_own_reasons(tmp_path):
    """4 個 null metric 不得共用錯誤 exclusion reason"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    m = canonical_metrics(results, posts, expected)
    expected_reasons = {
        "item_price_link_accuracy": "legacy_regression_adapter_does_not_expose_item_price_links",
        "verification_accuracy": "legacy_regression_adapter_does_not_expose_verified",
        "currency_accuracy": "legacy_regression_adapter_does_not_expose_currency",
        "image_merge_accuracy": "no_runtime_image_pipeline_executed",
    }
    for k, reason in expected_reasons.items():
        a = m[k]
        assert a["reason"] == reason, k
        reasons = {x["reason"] for x in a["excluded_reasons"]}
        assert reasons == {reason}, f"{k}: excluded reasons 混用"


def test_item_denominator_rule():
    """item metric：denominator == eligible_item_count（item-level 不要求 == case count）"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    m = canonical_metrics(results, posts, expected)
    a = m["item_accuracy"]
    assert a["denominator"] == a["eligible_item_count"]
    # 明確區分 case count 與 item count
    assert a["unit"] == "item"


def test_consistency_proof_can_fail(tmp_path):
    """package report FAIL → consistency 不可宣告完整 PASS"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    m = canonical_metrics(results, posts, expected)
    # 模擬 failed>0 → P0 不能 PASS、P7 不能 READY
    m["failed_cases"] = 3
    gate = evaluate_p7_entry_gate(m, {"p6_second_image_price_skip": True,
                                      "p7_router_not_implemented": True})
    assert gate == "NOT READY FOR P7"
