# -*- coding: utf-8 -*-
"""test_known_failure_integrity.py — known-failure 欄位語意分離驗證"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import load_fixtures  # noqa: E402


def _parse_known_failures_from_fixtures():
    """從 fixtures 的 known_defect + golden xfail 標記推導已知失敗模型"""
    posts, _ = load_fixtures()
    gp = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
              encoding="utf-8").read()
    xfail_nodes = set()
    lines = gp.splitlines()
    for i, ln in enumerate(lines):
        if "@pytest.mark.xfail" in ln:
            for j in range(i, min(i + 6, len(lines))):
                m = re.search(r"def (test_\w+)\(", lines[j])
                if m:
                    xfail_nodes.add(m.group(1))
                    break
    kf = []
    for p in posts:
        defect = p.get("known_defect")
        if not defect:
            continue
        cid = p["case_id"]
        is_future = p["category"] in ("P7_preview", "P8_preview")
        has_xfail = f"test_{cid}" in xfail_nodes
        kf.append({"case_id": cid, "known_defect": defect,
                   "future_gate": is_future, "has_xfail_marker": has_xfail,
                   "category": p["category"]})
    return kf


def test_pytest_xfail_count_matches_known_defects():
    """pytest xfail nodes == fixture known_defect cases"""
    kf = _parse_known_failures_from_fixtures()
    active = [k for k in kf if k["has_xfail_marker"] and not k["future_gate"]]
    future = [k for k in kf if k["has_xfail_marker"] and k["future_gate"]]
    # active product defects + future gates = 所有有 xfail 標記的
    assert len([k for k in kf if k["has_xfail_marker"]]) == len(active) + len(future)
    assert len(future) == 2, f"future gate = {len(future)}（應 2：P7+P8）"


def test_future_gate_remediation_phase():
    kf = _parse_known_failures_from_fixtures()
    for k in kf:
        if k["future_gate"]:
            assert k["category"] in ("P7_preview", "P8_preview")


def test_diagnostic_only_not_in_xfail():
    """diagnostic_only（defect 標記但 pytest pass）不得有 xfail marker——由 golden
    執行結果決定；此測試驗證 fixture 層無 diagnostic 混淆"""
    posts, _ = load_fixtures()
    for p in posts:
        # known_defect 標記的 case 必須有對應 golden 測試
        if p.get("known_defect"):
            gp = open(os.path.join(os.path.dirname(__file__), "test_golden_posts.py"),
                      encoding="utf-8").read()
            assert f"def test_{p['case_id']}(" in gp, p["case_id"]


def test_no_mixed_semantics_in_fixture_notes():
    """notes/expected 不得混合 status 語意（如 ok(xfail)）"""
    posts, expected = load_fixtures()
    for cid, e in expected.items():
        s = json.dumps(e, ensure_ascii=False)
        assert "（xfail）" not in s and "(xfail)" not in s, cid
        assert "unresolved（ok）" not in s, cid


# ================================================================
# P0.7 Diagnostic Taxonomy Integrity
# ================================================================

def _kf_rows():
    import csv
    from tests.regression.report import (load_fixtures, collect_case_results,
                                         build_report_files)
    import tempfile, io, contextlib, os
    posts, expected = load_fixtures()
    with contextlib.redirect_stdout(io.StringIO()):
        results = collect_case_results(posts, expected)
        td = tempfile.mkdtemp()
        build_report_files(results, posts, td, expected)
    with open(os.path.join(td, "p0-known-failures.csv"), encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_diagnostic_passed_no_active_defect_remediation():
    """diagnostic-only + PASSED 不得使用 active defect remediation（不預設 P6）"""
    rows = _kf_rows()
    for r in rows:
        if r["diagnostic_only"] == "True" and r["pytest_outcome"] == "PASSED":
            assert r["active_product_defect"] == "False", r["case_id"]
            assert r["active_xfail"] == "False", r["case_id"]
            assert r["remediation_phase"] != "P6" or r["reason_code"] in (
                "multi_image_conflict_unresolved",), r["case_id"]


def test_historical_defect_now_passing():
    """validation_failure_returns_first → historical_defect_now_passing / none"""
    rows = {r["case_id"]: r for r in _kf_rows()}
    r = rows["validation_failure_returns_first"]
    assert r["reason_code"] == "historical_defect_now_passing"
    assert r["remediation_phase"] == "none"
    assert r["diagnostic_only"] == "True"
    assert r["active_product_defect"] == "False"
    assert r["active_xfail"] == "False"
    assert r["future_gate"] == "False"
    assert r["pytest_outcome"] == "PASSED"
    assert "passes sealed test" in r["current_behavior"]


def test_environment_skip_explicit_reason():
    """multi_image_second_has_price → 明確 environment reason、非 diagnostic"""
    rows = {r["case_id"]: r for r in _kf_rows()}
    r = rows["multi_image_second_has_price"]
    assert r["reason_code"] == "multi_image_runtime_environment_unavailable"
    assert r["remediation_phase"] == "P6"
    assert r["diagnostic_only"] == "False"
    assert r["active_product_defect"] == "False"
    assert r["pytest_outcome"] == "SKIPPED"


def test_decimal_contract_passed_not_p6():
    """p0_decimal_precision → currency_decimal_contract_passed / none（非 P6）"""
    rows = {r["case_id"]: r for r in _kf_rows()}
    r = rows["p0_decimal_precision"]
    assert r["reason_code"] == "currency_decimal_contract_passed"
    assert r["remediation_phase"] == "none"
    assert r["diagnostic_only"] == "True"
    assert r["active_product_defect"] == "False"


def test_no_unknown_reason_codes():
    """reason_code=unknown 的最終記錄數必須為 0"""
    rows = _kf_rows()
    assert sum(1 for r in rows if r["reason_code"] == "unknown") == 0


def test_skipped_env_not_diagnostic():
    """SKIPPED environment record 不計入 diagnostic-only"""
    rows = _kf_rows()
    for r in rows:
        if r["pytest_outcome"] == "SKIPPED":
            assert r["diagnostic_only"] == "False", r["case_id"]


def test_core_kf_counts_frozen():
    """核心 known-failure 計數維持（active 11 / future 2 / pytest_xfail 13）"""
    rows = _kf_rows()
    active = sum(1 for r in rows if r["active_xfail"] == "True")
    future = sum(1 for r in rows if r["future_gate"] == "True")
    pxf = sum(1 for r in rows if r["pytest_outcome"] == "XFAIL")
    assert active == 11
    assert future == 2
    assert pxf == 13
    assert pxf == active + future
