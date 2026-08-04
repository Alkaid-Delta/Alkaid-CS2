# -*- coding: utf-8 -*-
"""test_evidence_level_integrity.py — P0.3 evidence-level 完整性（13 項）"""
import json
import os
import re
import sys

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from tests.regression.report import (  # noqa: E402
    load_fixtures, canonical_metrics, execution_level_of, metric_eligible,
    EVIDENCE_MAP, RUNTIME_LEVELS,
)


def _sample():
    posts, expected = load_fixtures()
    # 用真實 fixtures 建 case results（pytest_status 由 golden 決定——此處僅結構測試）
    cr = []
    for p in posts:
        cr.append({"case_id": p["case_id"], "pytest_status": "PASSED",
                   "case_status": "pass", "status": "ok",
                   "market_hash_name": "AK-47 | Redline (Field-Tested)",
                   "seller_price": 5000, "input_image_count": len(p.get("images", []))})
    return cr, posts, expected


def test_every_case_has_execution_level():
    posts, _ = load_fixtures()
    missing = [p["case_id"] for p in posts if execution_level_of(p["case_id"]) not in
               ("production_path", "controlled_integration", "legacy_snapshot",
                "contract_only", "fixture_only", "future_gate", "environment_skip")]
    assert not missing, f"缺 execution_level: {missing}"


def test_every_case_in_evidence_map():
    posts, _ = load_fixtures()
    missing = [p["case_id"] for p in posts if p["case_id"] not in EVIDENCE_MAP]
    assert not missing, f"缺 EVIDENCE_MAP 條目: {missing}"


def test_contract_only_not_in_runtime_pass():
    """contract-only 不得進 runtime pass count"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    # 全 pass 情況下：contract-only cases 不得算進 runtime_passed
    rt = m["execution_level_counts"].get("contract_only", 0)
    assert rt >= 1, "contract_only 分類缺失"
    # runtime_passed 不含 contract_only
    contract_ids = [c["case_id"] for c in cr
                    if execution_level_of(c["case_id"]) == "contract_only"]
    for cid in contract_ids:
        assert cid not in m["item_accuracy"]["eligible_case_ids"], \
            f"{cid} contract-only 誤入 item metric"


def test_contract_only_not_in_field_metrics():
    """contract-only 不進任何 field metric denominator"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    for k in ("item_accuracy", "seller_price_accuracy", "verification_accuracy",
              "parse_status_accuracy", "item_price_link_accuracy"):
        for cid in m[k]["eligible_case_ids"]:
            assert execution_level_of(cid) in RUNTIME_LEVELS, f"{cid} 非 runtime 卻 eligible"


def test_legacy_snapshot_image_not_in_image_metric():
    """legacy snapshot image case 不進 image merge metric"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    assert m["image_merge_accuracy"]["denominator"] == 0, "image metric 分母應 0（無 runtime image 執行）"
    assert m["image_merge_accuracy"]["reason"] == "no_runtime_image_pipeline_executed"


def test_mode_fixture_without_mode_execution_not_coverage():
    """mode fixture 未執行 mode 路徑 → contract_only（不算 mode coverage）"""
    for cid in ("p0_mode_off_legacy", "p0_mode_shadow", "p0_mode_v2_only"):
        assert execution_level_of(cid) == "contract_only", cid


def test_currency_legacy_snapshot_not_currency_service_evidence():
    """currency legacy snapshot 不算 CurrencyService 證據 → currency null + reason"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    assert m["currency_accuracy"]["value"] is None
    assert m["currency_accuracy"]["denominator"] == 0
    assert "legacy_regression_adapter_does_not_expose_currency" in m["currency_accuracy"]["reason"]


def test_denominator_equals_eligible_count():
    """分母 == eligible case 數（item 特例：分母 = expected item 層級總數）"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    for k in ("seller_price_accuracy", "verification_accuracy",
              "parse_status_accuracy", "item_price_link_accuracy",
              "image_merge_accuracy"):
        assert m[k]["denominator"] == len(m[k]["eligible_case_ids"]), k
    # item：分母 = sum(expected item count)——用真實 evaluate_case 驗證
    from tests.regression.report import evaluate_case
    real_posts, real_exp = load_fixtures()
    cr2 = [evaluate_case(p, real_exp.get(p["case_id"])) for p in real_posts]
    m2 = canonical_metrics(cr2, real_posts, real_exp)
    total_expected = sum(len(c.get("expected_items") or []) for c in cr2
                         if c["execution_level"] in RUNTIME_LEVELS)
    assert m2["item_accuracy"]["denominator"] == total_expected, \
        f"{m2['item_accuracy']['denominator']} != {total_expected}"


def test_eligible_excluded_disjoint():
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    for k in ("item_accuracy", "seller_price_accuracy", "verification_accuracy",
              "parse_status_accuracy", "item_price_link_accuracy",
              "image_merge_accuracy", "currency_accuracy"):
        elig = set(m[k]["eligible_case_ids"])
        excl = set(e["case_id"] for e in m[k]["excluded_reasons"])
        assert not (elig & excl), f"{k}: eligible/excluded 重疊"


def test_runtime_case_has_entrypoint():
    """runtime case 有 actual_entrypoint（EVIDENCE_MAP 的 legacy_snapshot/
    controlled_integration 代表 extract_legacy 執行）"""
    for cid, lv in EVIDENCE_MAP.items():
        if lv in RUNTIME_LEVELS:
            assert lv in ("legacy_snapshot", "controlled_integration"), cid


def test_claimed_requirement_evidence_level():
    """image/mode/currency 宣稱的 requirement 與 evidence level 一致"""
    posts, _ = load_fixtures()
    for p in posts:
        cid = p["case_id"]
        reqs = p.get("covered_requirements", [])
        lv = execution_level_of(cid)
        if any("image" in r or "multi-image" in r for r in reqs):
            assert lv in ("fixture_only", "environment_skip", "future_gate"), \
                f"{cid}: image requirement 但 level={lv}"
        if any("mode" in r for r in reqs):
            assert lv == "contract_only", f"{cid}: mode requirement 但 level={lv}"


def test_remediation_phase_mapping_correct():
    """known failure reason code → remediation phase 唯一合法"""
    kf = json.load(open(os.path.join(
        PROJECT_ROOT, "tests", "regression", "fixtures", "expected.json"),
        encoding="utf-8"))
    from tests.regression.report import REMEDIATION_PHASES
    assert REMEDIATION_PHASES["legacy_first_match_return"] == "P3"
    assert REMEDIATION_PHASES["model_router_not_implemented"] == "P7"
    assert REMEDIATION_PHASES["arbitrage_boundary_not_hardened"] == "P8"
    assert "currency_lost_on_dict_hit" in REMEDIATION_PHASES
    assert REMEDIATION_PHASES["currency_lost_on_dict_hit"] in ("P1", "evidence_limitation")


def test_p0_pass_not_p6_pass():
    """P0 PASS 不代表 P6 production 完成（image runtime 證據不足）"""
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    assert m["image_merge_accuracy"]["denominator"] == 0
    assert m["image_merge_accuracy"]["reason"] == "no_runtime_image_pipeline_executed"


def test_p7_gate_not_ready_without_runtime_image_evidence():
    cr, posts, expected = _sample()
    m = canonical_metrics(cr, posts, expected)
    from tests.regression.report import evaluate_p7_entry_gate
    decision = evaluate_p7_entry_gate(m, {
        "p6_second_image_price_skip": True,
        "p7_router_not_implemented": True,
        "image_runtime_evidence_insufficient": True,
    })
    assert decision == "NOT READY FOR P7"
