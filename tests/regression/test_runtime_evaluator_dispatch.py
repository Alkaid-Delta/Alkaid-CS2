# -*- coding: utf-8 -*-
"""test_runtime_evaluator_dispatch.py — evaluator dispatch（不同 level 不同 evaluator）"""
import sys
import os

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import (  # noqa: E402
    CASE_EVALUATORS, collect_case_results, load_fixtures, execution_level_of,
    RUNTIME_LEVELS,
)


def test_registry_has_all_levels():
    for lv in ("legacy_snapshot", "controlled_integration", "contract_only",
               "fixture_only", "future_gate", "environment_skip"):
        assert lv in CASE_EVALUATORS, lv


def test_report_no_fakeclient_import():
    """report.py 不得從 test_golden_posts import FakeClient（規格五）"""
    src = open(os.path.join(os.path.dirname(__file__), "report.py"),
               encoding="utf-8").read()
    assert "from tests.regression.test_golden_posts import" not in src


def test_legacy_cases_use_legacy_evaluator():
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    for c in results:
        if c["execution_level"] == "legacy_snapshot":
            assert c["evaluator_name"] == "evaluate_legacy_case", c["case_id"]
            assert c["runtime_executed"] is True
            assert c["actual_entrypoint"] == "extract_legacy"


def test_controlled_integration_honest_classification():
    """P0.6：6 個 P2 case 無法在 P0 安全重用正式 seam → 全降級 contract_only。
    controlled_integration 不得保留假分類（不得為維持 count=6 而保留不真實分類）。"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    ci = [c for c in results if c["execution_level"] == "controlled_integration"]
    assert len(ci) == 0, "不應有假 controlled integration"
    # 6 個 P2 case 全為 contract_only 且引用 sealed node
    for cid in ("validation_failure_returns_first", "p2_unknown_model_item",
                "p2_retry_fails_twice", "p2_vision_only_unverified",
                "p2_safe_fallback_attempted", "p2_validator_unavailable"):
        c = next(c for c in results if c["case_id"] == cid)
        assert c["execution_level"] == "contract_only", cid
        assert c["runtime_executed"] is False, cid
        assert c["runtime_reference_test"], f"{cid}: 缺 sealed node 引用"


def test_contract_only_no_runtime():
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    contract = [c for c in results if c["execution_level"] == "contract_only"]
    assert len(contract) == 10  # 6 P2 + p2_retry_succeeds + 3 mode
    for c in contract:
        assert c["runtime_executed"] is False
        assert c["runtime_reference_test"], c["case_id"]


def test_not_all_cases_extract_legacy():
    """report generator 不得對所有案例一律呼叫 extract_legacy"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    legacy_evaluated = sum(1 for c in results
                           if c["evaluator_name"] == "evaluate_legacy_case")
    assert legacy_evaluated == sum(1 for p in posts
                                   if execution_level_of(p["case_id"]) == "legacy_snapshot")
    assert legacy_evaluated < len(posts), "全部案例都走 legacy evaluator"


def test_fixture_only_no_runtime():
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    for c in results:
        if c["execution_level"] == "fixture_only":
            assert c["runtime_executed"] is False
            assert c["evaluator_result"] == "fixture_truth_only"


def test_future_gate_xfail():
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    for c in results:
        if c["execution_level"] == "future_gate":
            assert c["pytest_status"] == "XFAIL"


def test_runtime_dispatch_count():
    """P0.6：contract 10（6 P2 + retry_succeeds + 3 mode）、controlled 0、legacy 29"""
    posts, expected = load_fixtures()
    results = collect_case_results(posts, expected)
    from collections import Counter
    cnt = Counter(c["evaluator_name"] for c in results)
    assert cnt["evaluate_legacy_case"] == 29
    assert cnt["evaluate_contract_case"] == 10
    assert cnt["evaluate_fixture_only_case"] == 4
    assert cnt["evaluate_future_gate_case"] == 2
    assert cnt["evaluate_environment_skip_case"] == 1
    assert cnt.get("evaluate_controlled_integration_case", 0) == 0
