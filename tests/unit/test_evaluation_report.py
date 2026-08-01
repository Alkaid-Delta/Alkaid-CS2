"""test_evaluation_report.py — report 測試（Phase 6.4B）"""
import json
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.models import EvaluationCase  # noqa: E402
from alkaid_cs2.evaluation.prediction import EvaluationPrediction  # noqa: E402
from alkaid_cs2.evaluation.report import (  # noqa: E402
    READINESS_NOT_READY, READINESS_SAFE_PILOT, READINESS_SHADOW,
    generate_evaluation_report, write_evaluation_report_json,
    write_evaluation_report_markdown,
)
from alkaid_cs2.evaluation.scoring import CaseEvaluationResult, score_case  # noqa: E402
from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory  # noqa: E402

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation")


def _mini_cases(n=5):
    cases = load_evaluation_directory(FIXTURES)[:n]
    preds = {"legacy": [], "text_v2": [], "vision_raw": [],
             "vision_production": []}
    results = {"legacy": [], "text_v2": [], "vision_raw": [],
               "vision_production": []}
    for c in cases:
        for name in ("legacy", "text_v2", "vision_raw", "vision_production"):
            p = EvaluationPrediction(
                case_id=c.case_id, parser_name=name,
                blocked=not c.expected_safe_for_production,
                source="skipped" if not c.expected_safe_for_production else "v2",
                market_hash_names=[it.market_hash_name for it in c.expected_items
                                   if it.market_hash_name],
                latency_ms=10.0)
            preds[name].append(p)
            exp_safe = (c.expected_raw_vision_safe if name == "vision_raw"
                        else c.expected_safe_for_production)
            results[name].append(score_case(c, name, p, expected_safe=exp_safe))
    return cases, preds, results


def test_report_fields_complete():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    assert "dataset" in r and "parsers" in r and "readiness" in r
    assert set(r["parsers"].keys()) == {"legacy", "text_v2", "vision_raw", "vision_production"}


def test_safe_false_positive_listed():
    cases, preds, results = _mini_cases()
    # 強制一個 FP：not_safe case 被預測 safe
    not_safe = [c for c in cases if not c.expected_safe_for_production]
    if not_safe:
        c = not_safe[0]
        for name in ("legacy", "text_v2", "vision_raw", "vision_production"):
            for i, cc in enumerate(cases):
                if cc.case_id == c.case_id:
                    preds[name][i] = EvaluationPrediction(
                        case_id=c.case_id, parser_name=name, blocked=False,
                        source="v2", latency_ms=1.0)
                    results[name][i] = score_case(c, name, preds[name][i],
                                    expected_safe=(c.expected_raw_vision_safe if name == "vision_raw" else c.expected_safe_for_production))
    r = generate_evaluation_report(cases, preds, results)
    vis_fp = r["parsers"]["vision_production"]["safe"]["safe_false_positive_cases"]
    assert isinstance(vis_fp, list), "safe FP cases 可列出"


def test_latency_p50():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    st = r["parsers"]["vision_production"]["stats"]
    assert st["p50_latency_ms"] == 10.0


def test_latency_p95():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    st = r["parsers"]["vision_production"]["stats"]
    assert st["p95_latency_ms"] == 10.0


def test_warning_counts():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results,
                                   warnings_seen=["vision_blocked:a", "v2_error:b"])
    codes = dict(r["top_warning_codes"])
    assert codes.get("vision_blocked", 0) == 1


def test_json_serializable(tmp_path):
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    p = tmp_path / "r.json"
    write_evaluation_report_json(r, p)
    json.loads(p.read_text(encoding="utf-8"))  # 可序列化


def test_markdown_created(tmp_path):
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    p = tmp_path / "r.md"
    write_evaluation_report_markdown(r, p)
    text = p.read_text(encoding="utf-8")
    assert "Readiness" in text or "readiness" in text
    assert "## vision_production" in text


def test_no_sensitive_payload():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    s = json.dumps(r, default=str)
    for bad in ("cookie", "api_key", "token", "base64"):
        assert bad.lower() not in s.lower(), f"報告不得含 {bad}"


def test_readiness_not_ready_under_25():
    cases, preds, results = _mini_cases(5)  # 5 < 25
    r = generate_evaluation_report(cases, preds, results)
    assert r["readiness"] == READINESS_NOT_READY


def test_readiness_shadow_ready_at_25():
    cases = load_evaluation_directory(FIXTURES)  # 34 >= 25
    preds = {"legacy": [], "text_v2": [], "vision_raw": [],
             "vision_production": []}
    results = {"legacy": [], "text_v2": [], "vision_raw": [],
               "vision_production": []}
    for c in cases:
        for name in ("legacy", "text_v2", "vision_raw", "vision_production"):
            p = EvaluationPrediction(case_id=c.case_id, parser_name=name,
                                     blocked=False, source="v2", latency_ms=1.0)
            preds[name].append(p)
            exp_safe = (c.expected_raw_vision_safe if name == "vision_raw"
                        else c.expected_safe_for_production)
            results[name].append(score_case(c, name, p, expected_safe=exp_safe))
    r = generate_evaluation_report(cases, preds, results)
    assert r["readiness"] in (READINESS_SHADOW, READINESS_SAFE_PILOT)
    assert r["readiness"] != READINESS_NOT_READY


def test_safe_candidate_requires_50():
    # 34 案例即使全過也最多 SHADOW（<50）
    cases = load_evaluation_directory(FIXTURES)
    assert len(cases) < 50
    preds = {"legacy": [], "text_v2": [], "vision_raw": [],
             "vision_production": []}
    results = {"legacy": [], "text_v2": [], "vision_raw": [],
               "vision_production": []}
    for c in cases:
        for name in ("legacy", "text_v2", "vision_raw", "vision_production"):
            p = EvaluationPrediction(case_id=c.case_id, parser_name=name,
                                     blocked=False, source="v2", latency_ms=1.0)
            preds[name].append(p)
            exp_safe = (c.expected_raw_vision_safe if name == "vision_raw"
                        else c.expected_safe_for_production)
            results[name].append(score_case(c, name, p, expected_safe=exp_safe))
    r = generate_evaluation_report(cases, preds, results)
    assert r["readiness"] != READINESS_SAFE_PILOT, \
        "cases < 50 不得輸出 SAFE_PILOT_CANDIDATE"


# ================================================================
# Phase 6.4B.1 — report 強化
# ================================================================
def test_case_by_case_results_present():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    rows = r["case_by_case"]
    assert isinstance(rows, list) and rows, "case-by-case 存在"
    first = rows[0]
    for k in ("case_id", "parser_name", "expected_safe", "predicted_safe",
              "item_exact", "price_correct", "linking_ok", "conflict_detected",
              "fallback"):
        assert k in first, f"case-by-case 缺 {k}"
    s = json.dumps(first, default=str)
    assert "payload" not in s.lower(), "不得含 payload"


def test_known_limitations_present():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    lims = r["known_limitations"]
    for lim in ("all_cases_synthetic", "vision_payloads_are_fixture_outputs",
                "offline_legacy_is_not_deepseek_legacy",
                "latency_is_local_runtime_metadata",
                "image_type_accuracy_is_fixture_biased"):
        assert lim in lims, f"缺 known limitation: {lim}"


def test_p95_nearest_rank():
    from alkaid_cs2.evaluation.report import _p95
    assert _p95([]) == 0.0
    assert _p95([1.0]) == 1.0
    # n=20：ceil(19)=19-1=18 → 第 19 小（0-based 18）
    vals20 = [float(i) for i in range(20)]  # 0..19
    assert _p95(vals20) == 18.0, f"n=20 P95 (ceil(19)-1=18): {_p95(vals20)}"
    # n=34：ceil(32.3)=33-1=32 → 第 33 小（0-based 32）
    vals34 = [float(i) for i in range(34)]
    assert _p95(vals34) == 32.0, f"n=34 P95 (ceil(32.3)-1=32): {_p95(vals34)}"


# ================================================================
# Phase 6.4B.3 — Seller price 語意 & Markdown
# ================================================================
def test_markdown_displays_fp_denominator(tmp_path):
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    p = tmp_path / "r.md"
    write_evaluation_report_markdown(r, p)
    text = p.read_text(encoding="utf-8")
    assert "negative item opportunities" in text, "Markdown 顯示 denominator"
    assert "extra unmatched seller asks" in text
    assert "seller asks on wrong item" in text


def test_readiness_uses_true_negative_opportunities():
    # denominator=0 → readiness 不得 SAFE_PILOT（無法驗證無錯）
    from alkaid_cs2.evaluation.report import _compute_readiness
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    stats = dict(r["parsers"]["vision_production"]["stats"])
    stats["seller_price_false_positive_denominator"] = 0
    stats["seller_price_false_positive_rate"] = 0.0
    safe = r["parsers"]["vision_production"]["safe"]
    rd = _compute_readiness(cases, safe, stats, crash_count=0)
    assert rd != "SAFE_PILOT_CANDIDATE", "denominator=0 不得 SAFE_PILOT"


def test_seller_ask_wrong_item_in_case_by_case():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    assert "seller_ask_wrong_item" in r["case_by_case"][0], \
        "case-by-case 含 wrong_item 欄位"


# ================================================================
# Phase 6.4B.4 — Negative Item FPR & 報告
# ================================================================
def test_readiness_uses_negative_item_fp_rate():
    # FPR 用 negative_item_fp；>1% → 不得 SAFE_PILOT
    from alkaid_cs2.evaluation.report import _compute_readiness
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    stats = dict(r["parsers"]["vision_production"]["stats"])
    stats["seller_negative_item_false_positive_count"] = 2
    stats["seller_price_false_positive_denominator"] = 2
    stats["seller_price_false_positive_rate"] = 1.0
    safe = r["parsers"]["vision_production"]["safe"]
    rd = _compute_readiness(cases, safe, stats, crash_count=0)
    assert rd != "SAFE_PILOT_CANDIDATE", "FPR 100% 不得 SAFE_PILOT"


def test_markdown_displays_extra_unmatched_asks(tmp_path):
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    p = tmp_path / "r.md"
    write_evaluation_report_markdown(r, p)
    text = p.read_text(encoding="utf-8")
    assert "extra unmatched seller asks" in text


def test_case_by_case_displays_extra_unmatched_asks():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    assert "extra_unmatched_asks" in r["case_by_case"][0]
    assert "seller_neg_item_fp" in r["case_by_case"][0]


# ================================================================
# Phase 6.4C1 — Dataset Quality / Readiness
# ================================================================
def test_dataset_quality_block_present():
    cases, preds, results = _mini_cases()
    r = generate_evaluation_report(cases, preds, results)
    q = r["dataset_quality"]
    for k in ("total_loaded_cases", "evaluated_cases",
              "readiness_eligible_cases", "excluded_from_evaluation",
              "synthetic_cases", "anonymized_real_cases", "adversarial_cases",
              "double_reviewed_cases", "single_reviewed_cases",
              "disputed_cases", "privacy_error_count",
              "privacy_warning_count"):
        assert k in q, f"dataset_quality 缺 {k}"
    assert q["total_loaded_cases"] == len(cases)


def test_source_counts_correct():
    from alkaid_cs2.evaluation.models import EvaluationSource
    from alkaid_cs2.evaluation.report import _dataset_quality
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    real = load_evaluation_directory(FIXTURES_REAL)
    adv = load_evaluation_directory(FIXTURES_ADV) \
        if os.path.isdir(FIXTURES_ADV) else []
    q = _dataset_quality(real + adv, [])
    # 6.4C1.1 誠實性：10 個 agent_generated 案例標 manual_fixture（非 anonymized_real）
    assert q["anonymized_real_cases"] == 0
    assert q["manual_fixture_cases"] == 10
    assert q["adversarial_cases"] == 6
    assert q["double_reviewed_cases"] == 8
    assert q["single_reviewed_cases"] == 1
    assert q["disputed_cases"] == 1


def test_disputed_excluded_from_readiness():
    from alkaid_cs2.evaluation.report import _readiness_eligible
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    real = load_evaluation_directory(FIXTURES_REAL)
    eligible = _readiness_eligible(real)
    assert all(c.case_id != "real_inventory_grid_010" for c in eligible), \
        "disputed 不進 readiness"
    assert all(c.case_id != "real_simplified_rmb_009" for c in eligible), \
        "single_review real 不進 readiness"
    assert len(eligible) == 8, "只有 8 個 double_reviewed real"


def test_readiness_uses_eligible_case_count():
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    from alkaid_cs2.evaluation.scoring import score_case
    real = load_evaluation_directory(FIXTURES_REAL)
    preds = {n: [] for n in ("legacy", "text_v2", "vision_raw",
                             "vision_production")}
    results = {n: [] for n in preds}
    for c in real:
        for name in preds:
            p = EvaluationPrediction(case_id=c.case_id, parser_name=name,
                                     parse_status="parsed", source="v2",
                                     latency_ms=1.0)
            preds[name].append(p)
            results[name].append(score_case(
                c, name, p,
                expected_safe=c.expected_raw_vision_safe
                if name == "vision_raw" else c.expected_safe_for_production))
    r = generate_evaluation_report(real, preds, results)
    # 只有 8 eligible → <25 → NOT_READY（用 eligible 不是 total=10）
    assert r["dataset_quality"]["readiness_eligible_cases"] == 8
    assert r["readiness"] == "NOT_READY"


def test_readiness_reasons_present():
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    from alkaid_cs2.evaluation.scoring import score_case
    cases = load_evaluation_directory(FIXTURES)
    preds = {n: [] for n in ("legacy", "text_v2", "vision_raw",
                             "vision_production")}
    results = {n: [] for n in preds}
    for c in cases:
        for name in preds:
            p = EvaluationPrediction(case_id=c.case_id, parser_name=name,
                                     parse_status="parsed", source="v2",
                                     latency_ms=1.0)
            preds[name].append(p)
            results[name].append(score_case(
                c, name, p,
                expected_safe=c.expected_raw_vision_safe
                if name == "vision_raw" else c.expected_safe_for_production))
    r = generate_evaluation_report(cases, preds, results)
    reasons = r.get("readiness_reasons", [])
    assert isinstance(reasons, list) and reasons, "readiness_reasons 非空"
    assert "insufficient_eligible_cases" in reasons, "34 < 50 原因列出"


def test_real_data_validation_status_partial():
    from alkaid_cs2.evaluation.report import _real_data_validation_status
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    real = load_evaluation_directory(FIXTURES_REAL)
    # 6.4C1.2：anonymized_real=0（全部 manual_fixture）→ insufficient
    assert _real_data_validation_status(real) == "insufficient", \
        "anonymized_real=0 → insufficient（manual_fixture 不計入）"


def test_real_data_validation_status_partial_when_real_present():
    # 有 anonymized_real 但未達門檻 → partial
    from alkaid_cs2.evaluation.report import _real_data_validation_status
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    cases = [
        EvaluationCase(case_id=f"r{i}", source=EvaluationSource.ANONYMIZED_REAL,
                       author="anonymous", link=f"redacted://r{i}",
                       raw_text="售 A 算5000", expected_safe_for_production=True,
                       redaction_version="1.0",
                       ground_truth_review_status="double_review")
        for i in range(10)
    ]
    assert _real_data_validation_status(cases) == "partial", "10 real < 20 → partial"


def test_analyzer_coverage_not_hardcoded():
    from alkaid_cs2.evaluation.report import _dataset_quality
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    real = load_evaluation_directory(FIXTURES_REAL)
    cov = {"external_analyzer_cases": 0, "external_analyzer_images": 0,
           "cached_analyzer_cases": 5, "cached_analyzer_images": 12,
           "analyzer_eligible_images": 20}
    q = _dataset_quality(real, [], analyzer_coverage=cov)
    assert q["cached_analyzer_cases"] == 5, "不得硬編碼 0"
    assert q["cached_analyzer_images"] == 12
    assert q["analyzer_eligible_images"] == 20
    assert q["analyzer_coverage_rate"] == 0.6


# ================================================================
# Phase 6.4C1.3 — Real coverage / status 契約
# ================================================================
def _real_cases(n: int, double: int = 0):
    from alkaid_cs2.evaluation.models import EvaluationCase, EvaluationSource
    return [
        EvaluationCase(case_id=f"r{i}", source=EvaluationSource.ANONYMIZED_REAL,
                       author="anonymous", link=f"redacted://r{i}",
                       raw_text="售 A 算5000", expected_safe_for_production=True,
                       redaction_version="1.0",
                       ground_truth_review_status="double_review"
                       if i < double else "single_review")
        for i in range(n)
    ]


def test_real_20_double_15_zero_coverage_is_partial():
    from alkaid_cs2.evaluation.report import _real_data_validation_status
    assert _real_data_validation_status(_real_cases(20, 15),
                                        real_coverage_rate=0.0) == "partial", \
        "coverage 0% → partial"


def test_real_20_double_15_79_percent_is_partial():
    from alkaid_cs2.evaluation.report import _real_data_validation_status
    assert _real_data_validation_status(_real_cases(20, 15),
                                        real_coverage_rate=0.79) == "partial", \
        "79% < 80% → partial"


def test_real_20_double_15_80_percent_is_complete():
    from alkaid_cs2.evaluation.report import _real_data_validation_status
    assert _real_data_validation_status(_real_cases(20, 15),
                                        real_coverage_rate=0.80) == "complete", \
        "80% → complete"


def test_manual_cache_does_not_increase_real_coverage():
    # manual_fixture 的 cache hit 不得提高 real coverage
    from alkaid_cs2.evaluation.report import _dataset_quality
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    cases = load_evaluation_directory(FIXTURES_REAL)  # 10 manual_fixture
    cov = {"cached_analyzer_cases": 10, "cached_analyzer_images": 20,
           "analyzer_eligible_images": 20,
           "real_analyzer_eligible_images": 0,
           "real_cached_analyzer_images": 0,
           "real_external_analyzer_images": 0,
           "real_analyzer_coverage_rate": 0.0}
    q = _dataset_quality(cases, [], analyzer_coverage=cov)
    assert q["real_analyzer_coverage_rate"] == 0.0, "manual cache 不進 real coverage"
    assert q["real_cached_analyzer_images"] == 0


def test_synthetic_cache_does_not_increase_real_coverage():
    from alkaid_cs2.evaluation.report import _dataset_quality
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    cases = load_evaluation_directory(FIXTURES)  # synthetic only
    cov = {"cached_analyzer_cases": 30, "cached_analyzer_images": 40,
           "analyzer_eligible_images": 40,
           "real_analyzer_eligible_images": 0,
           "real_cached_analyzer_images": 0,
           "real_external_analyzer_images": 0,
           "real_analyzer_coverage_rate": 0.0}
    q = _dataset_quality(cases, [], analyzer_coverage=cov)
    assert q["real_analyzer_coverage_rate"] == 0.0, "synthetic cache 不進 real coverage"


def test_privacy_counts_correct():
    from alkaid_cs2.evaluation.report import _dataset_quality
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    from alkaid_cs2.evaluation.privacy import scan_fixture_for_sensitive_data
    real = load_evaluation_directory(FIXTURES_REAL)
    findings = []
    for c in real:
        findings.extend(scan_fixture_for_sensitive_data(c))
    q = _dataset_quality(real, findings)
    assert q["privacy_error_count"] == 0
    assert q["privacy_warning_count"] == 0


# ================================================================
# Phase 6.4C1.4 — Real status / readiness 傳遞
# ================================================================
def _mini_cases_with_real(n_real=0):
    """synthetic + n 個 anonymized_real（含 coverage）mini dataset。"""
    from decimal import Decimal
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationCase, EvaluationSource, Currency,
    )
    cases = list(load_evaluation_directory(FIXTURES))
    for i in range(n_real):
        cases.append(EvaluationCase(
            case_id=f"real{i}", source=EvaluationSource.ANONYMIZED_REAL,
            author="anonymous", link=f"redacted://real{i}",
            raw_text="售 A 算5000", expected_safe_for_production=True,
            redaction_version="1.0",
            ground_truth_review_status="double_review"))
    preds = {n: [] for n in ("legacy", "text_v2", "vision_raw",
                             "vision_production")}
    results = {n: [] for n in preds}
    for c in cases:
        for name in preds:
            safe = c.expected_safe_for_production
            p = EvaluationPrediction(
                case_id=c.case_id, parser_name=name,
                parse_status="parsed", source="v2", latency_ms=1.0,
                blocked=not safe,  # 不安全的案例 blocked → safe FP=0
                market_hash_names=["AK-47 | Redline (Field-Tested)"] if safe else [],
                seller_prices=[Decimal("5000")] if safe else [],
                seller_price_item_indexes=[0] if safe else [],
                currencies=[Currency.TWD] if safe else [],
                price_types=["seller_ask"] if safe else [],
                price_indexes=[0] if safe else [])
            preds[name].append(p)
            results[name].append(score_case(
                c, name, p,
                expected_safe=c.expected_raw_vision_safe
                if name == "vision_raw" else c.expected_safe_for_production))
    return cases, preds, results


def test_complete_real_status_not_recomputed_without_coverage():
    # real coverage 0（未提供）→ status 不得誤判 complete
    cases, preds, results = _mini_cases_with_real(n_real=1)
    r = generate_evaluation_report(cases, preds, results,
                                   analyzer_coverage={
                                       "real_analyzer_coverage_rate": 0.0})
    assert r["real_data_validation_status"] == "partial", \
        "coverage 0% → partial（不得漏 coverage 重算）"


def test_partial_real_status_returns_real_pending():
    cases, preds, results = _mini_cases_with_real(n_real=1)
    r = generate_evaluation_report(cases, preds, results,
                                   analyzer_coverage={
                                       "real_analyzer_coverage_rate": 0.0})
    assert r["readiness"] == "SHADOW_READY_REAL_DATA_PENDING", \
        "有 real 且 status != complete → REAL_DATA_PENDING"


def test_no_real_returns_shadow_ready():
    from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
    cases = list(load_evaluation_directory(FIXTURES))
    preds = {n: [] for n in ("legacy", "text_v2", "vision_raw",
                             "vision_production")}
    results = {n: [] for n in preds}
    for c in cases:
        for name in preds:
            p = EvaluationPrediction(case_id=c.case_id, parser_name=name,
                                     parse_status="parsed", source="v2",
                                     latency_ms=1.0)
            preds[name].append(p)
            results[name].append(score_case(
                c, name, p,
                expected_safe=c.expected_raw_vision_safe
                if name == "vision_raw" else c.expected_safe_for_production))
    r = generate_evaluation_report(cases, preds, results)
    assert r["readiness"] == "SHADOW_READY", "無 anonymized_real → SHADOW_READY"


FIXTURES_REAL = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation_real")
FIXTURES_ADV = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation_adversarial")
