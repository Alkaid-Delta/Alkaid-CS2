"""
report.py — Evaluation Report 產生（Phase 6.4B / 6.4B.1）

四 parser：legacy / text_v2 / vision_raw / vision_production。
Readiness：NOT_READY / SHADOW_READY / SAFE_PILOT_CANDIDATE（<50 最多 SHADOW_READY）。
"""
import json
import math
import statistics
from pathlib import Path

from alkaid_cs2.evaluation.models import EvaluationCase
from alkaid_cs2.evaluation.prediction import EvaluationPrediction
from alkaid_cs2.evaluation.scoring import CaseEvaluationResult, MetricCounts

READINESS_NOT_READY = "NOT_READY"
READINESS_SHADOW = "SHADOW_READY"
READINESS_SAFE_PILOT = "SAFE_PILOT_CANDIDATE"
READINESS_SHADOW_REAL_PENDING = "SHADOW_READY_REAL_DATA_PENDING"

PARSER_ORDER = ("legacy", "text_v2", "vision_raw", "vision_production")

KNOWN_LIMITATIONS = [
    "vision_payloads_are_fixture_outputs",
    "offline_legacy_is_not_deepseek_legacy",
    "latency_is_local_runtime_metadata",
    "image_type_accuracy_is_fixture_biased",
]
# Phase 6.4C1.1：有 real/manual 案例時取代 all_cases_synthetic
REAL_DATA_LIMITATIONS = [
    "anonymized_real_sample_size_small",
    "external_analyzer_not_yet_executed",
    "analyzer_cache_is_fixture_mirrored",
    "image_hash_uses_url_placeholder",
    "price_comparison_first_price_only",
]


def _known_limitations(cases: list[EvaluationCase]) -> list[str]:
    """known limitations（6.4C1.3 三態）：

    - 純 synthetic（無 manual/adversarial/real）→ all_cases_synthetic
    - synthetic + manual/adversarial（無 anonymized_real）→
      no_anonymized_real_cases + all_cases_are_synthetic_or_manual
    - 含 anonymized_real → REAL_DATA_LIMITATIONS
    """
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource,
    )
    has_real = any(c.source == EvaluationSource.ANONYMIZED_REAL for c in cases)
    has_manual_adv = any(c.source in (EvaluationSource.MANUAL_FIXTURE,
                                      EvaluationSource.ADVERSARIAL_SYNTHETIC)
                         for c in cases)
    base = [
        "vision_payloads_are_fixture_outputs",
        "offline_legacy_is_not_deepseek_legacy",
        "latency_is_local_runtime_metadata",
        "image_type_accuracy_is_fixture_biased",
    ]
    if has_real:
        return list(REAL_DATA_LIMITATIONS) + base
    if has_manual_adv:
        return [
            "no_anonymized_real_cases",
            "all_cases_are_synthetic_or_manual",
            "external_analyzer_not_yet_executed",
            "analyzer_cache_is_fixture_mirrored",
            "image_hash_uses_url_placeholder",
            "price_comparison_first_price_only",
        ] + base
    return base + ["all_cases_synthetic"]


def _pct(num: int, den: int) -> float:
    return num / den if den else 0.0


def _dataset_quality(cases: list[EvaluationCase],
                     privacy_findings: list,
                     evaluated_count: int | None = None,
                     analyzer_coverage: dict | None = None) -> dict[str, object]:
    """dataset quality 區塊（Phase 6.4C1.2）。

    - total_loaded_cases：載入的全部案例（含 single/disputed）
    - evaluated_cases：實際跑 parser 的案例
    - readiness_eligible_cases：可進 readiness 的案例
    - analyzer coverage：external/cached cases 與 images（不得硬編碼 0）
    """
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource, GroundTruthReviewStatus,
    )
    syn = sum(1 for c in cases if c.source == EvaluationSource.SYNTHETIC)
    real = sum(1 for c in cases
               if c.source == EvaluationSource.ANONYMIZED_REAL)
    adv = sum(1 for c in cases
              if c.source == EvaluationSource.ADVERSARIAL_SYNTHETIC)
    manual = sum(1 for c in cases
                 if c.source == EvaluationSource.MANUAL_FIXTURE)
    double = sum(1 for c in cases
                 if c.ground_truth_review_status == GroundTruthReviewStatus.DOUBLE_REVIEW)
    single = sum(1 for c in cases
                 if c.ground_truth_review_status == GroundTruthReviewStatus.SINGLE_REVIEW)
    disputed = sum(1 for c in cases
                   if c.ground_truth_review_status == GroundTruthReviewStatus.DISPUTED)
    eligible = _readiness_eligible(cases)
    excluded = sum(1 for c in cases if c.excluded_from_readiness)
    n_err = sum(1 for f in privacy_findings if f.severity == "error")
    n_warn = sum(1 for f in privacy_findings if f.severity == "warning")
    evaluated = evaluated_count if evaluated_count is not None else len(eligible)
    cov = analyzer_coverage or {}
    ext_cases = cov.get("external_analyzer_cases", 0)
    ext_images = cov.get("external_analyzer_images", 0)
    cached_cases = cov.get("cached_analyzer_cases", 0)
    cached_images = cov.get("cached_analyzer_images", 0)
    eligible_images = cov.get("analyzer_eligible_images", 0)
    covered_images = ext_images + cached_images
    # Phase 6.4C1.3：real coverage 獨立（只統計 anonymized_real）
    real_eligible = cov.get("real_analyzer_eligible_images", 0)
    real_cached = cov.get("real_cached_analyzer_images", 0)
    real_external = cov.get("real_external_analyzer_images", 0)
    real_covered = real_cached + real_external
    return {
        "total_loaded_cases": len(cases),
        "evaluated_cases": evaluated,
        "readiness_eligible_cases": len(eligible),
        "excluded_from_evaluation": excluded,
        "synthetic_cases": syn,
        "anonymized_real_cases": real,
        "adversarial_cases": adv,
        "manual_fixture_cases": manual,
        "double_reviewed_cases": double,
        "single_reviewed_cases": single,
        "disputed_cases": disputed,
        "privacy_error_count": n_err,
        "privacy_warning_count": n_warn,
        "external_analyzer_cases": ext_cases,
        "external_analyzer_images": ext_images,
        "cached_analyzer_cases": cached_cases,
        "cached_analyzer_images": cached_images,
        "analyzer_eligible_images": eligible_images,
        "analyzer_coverage_rate": round(covered_images / eligible_images, 4)
        if eligible_images else 0.0,
        "real_analyzer_eligible_images": real_eligible,
        "real_cached_analyzer_images": real_cached,
        "real_external_analyzer_images": real_external,
        "real_analyzer_coverage_rate": round(real_covered / real_eligible, 4)
        if real_eligible else 0.0,
        "fixture_only_cases": sum(1 for c in cases
                                  if not c.images or
                                  all(img.vision_payload is None for img in c.images)),
    }


def _real_data_validation_status(cases: list[EvaluationCase],
                                 real_coverage_rate: float | None = None) -> str:
    """real_data_validation_status：insufficient / partial / complete（6.4C1.3）。

    - anonymized_real == 0 → insufficient（manual_fixture 不得計入）
    - anonymized_real > 0 且任一未達 → partial：
      real cases < 20 / double-reviewed real < 15 / real analyzer coverage < 80%
    - 全達標 → complete
    """
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource, GroundTruthReviewStatus,
    )
    real_total = sum(1 for c in cases
                     if c.source == EvaluationSource.ANONYMIZED_REAL)
    if real_total == 0:
        return "insufficient"
    real_double = sum(1 for c in cases
                      if c.source == EvaluationSource.ANONYMIZED_REAL and
                      c.ground_truth_review_status == GroundTruthReviewStatus.DOUBLE_REVIEW)
    if real_total < 20 or real_double < 15:
        return "partial"
    if real_coverage_rate is None or real_coverage_rate < 0.80:
        return "partial"  # real analyzer coverage < 80%
    return "complete"


def _readiness_eligible(cases: list[EvaluationCase]) -> list[EvaluationCase]:
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource, GroundTruthReviewStatus,
    )
    return [c for c in cases
            if not c.excluded_from_readiness
            and c.ground_truth_review_status != GroundTruthReviewStatus.DISPUTED
            and (c.source != EvaluationSource.ANONYMIZED_REAL or
                 c.ground_truth_review_status == GroundTruthReviewStatus.DOUBLE_REVIEW)]


def _percent_str(ratio: float) -> str:
    return f"{ratio * 100:.2f}%"


def _p50(values: list[float]) -> float:
    return statistics.median(values) if values else 0.0


def _p95(values: list[float]) -> float:
    """nearest-rank P95：index = ceil(0.95 * n) - 1。"""
    if not values:
        return 0.0
    s = sorted(values)
    idx = math.ceil(0.95 * len(s)) - 1
    idx = max(0, min(len(s) - 1, idx))
    return s[idx]


def _safe_matrix(results: list[CaseEvaluationResult],
                 include_none: bool = False) -> dict[str, object]:
    """safe confusion matrix（expected_safe=None 的 raw 案例可排除）。"""
    m = MetricCounts()
    safe_fp_cases: list[dict[str, object]] = []
    for r in results:
        if r.expected_safe is None:
            if include_none:
                continue
            continue
        if r.expected_safe and r.predicted_safe:
            m.true_positive += 1
        elif (not r.expected_safe) and r.predicted_safe:
            m.false_positive += 1
            safe_fp_cases.append({"case_id": r.case_id, "notes": r.notes[:3]})
        elif r.expected_safe and (not r.predicted_safe):
            m.false_negative += 1
        else:
            m.true_negative += 1
    fp_rate = _pct(m.false_positive, m.false_positive + m.true_negative)
    safe_fp_cases.sort(key=lambda d: str(d["case_id"]))
    return {
        "true_positive": m.true_positive, "false_positive": m.false_positive,
        "false_negative": m.false_negative, "true_negative": m.true_negative,
        "safe_false_positive_rate": fp_rate,
        "safe_false_positive_cases": safe_fp_cases,
    }


def _parser_stats(results: list[CaseEvaluationResult],
                  predictions: list[EvaluationPrediction]) -> dict[str, object]:
    n = len(results) or 1
    total_items = sum(r.item_exact_matches + r.item_partial_matches +
                      r.item_false_negatives for r in results)
    item_exact = sum(r.item_exact_matches for r in results)
    item_partial = sum(r.item_partial_matches for r in results)
    item_fp = sum(r.item_false_positives for r in results)
    price_exact = sum(r.seller_price_exact_matches for r in results)
    price_missed = sum(r.seller_price_missed for r in results)
    price_wrong_amt = sum(r.seller_price_wrong_amount for r in results)
    price_wrong_cur = sum(r.seller_price_wrong_currency for r in results)
    false_asks = sum(r.false_seller_asks for r in results)
    ref_promoted = sum(r.reference_promoted_to_seller for r in results)
    neg_item_fp = sum(r.seller_negative_item_false_positives for r in results)
    extra_unmatched = sum(r.extra_unmatched_seller_asks for r in results)
    wrong_item_asks = sum(r.seller_ask_on_wrong_item for r in results)
    neg_opps = sum(r.seller_price_negative_opportunities for r in results)
    price_den = price_exact + price_missed + price_wrong_amt + price_wrong_cur
    # seller FP denominator = negative opportunities（expected 無 seller ask 的 items）
    # FPR numerator = matched negative items 的 ask（不含 extra unmatched / wrong item）
    fp_den = neg_opps
    fp_num = neg_item_fp
    cur_ok = sum(r.currency_exact_matches for r in results)
    cur_wrong = sum(r.currency_wrong for r in results)
    wear_ok = sum(r.wear_exact_matches for r in results)
    wear_wrong = sum(r.wear_wrong for r in results)
    link_ok = sum(r.linking_correct for r in results)
    link_wrong = sum(r.linking_wrong for r in results)
    latencies = [r.latency_ms for r in results if r.latency_ms > 0]
    fallbacks = {"none": 0, "text_v2": 0, "skipped": 0}
    for p in predictions:
        fb = p.fallback_used or "none"
        fallbacks[fb] = fallbacks.get(fb, 0) + 1
    return {
        "cases": n,
        "item_exact_match_rate": _pct(item_exact, total_items),
        "item_match_recall": _pct(item_exact + item_partial, total_items),
        "item_strict_recall": _pct(item_exact, total_items),
        "item_false_positive_count": item_fp,
        "seller_price_exact_rate": _pct(price_exact, price_den),
        "seller_price_miss_rate": _pct(price_missed, price_den),
        "seller_price_wrong_amount_rate": _pct(price_wrong_amt, price_den),
        "seller_price_wrong_currency_rate": _pct(price_wrong_cur, price_den),
        "seller_price_false_positive_rate": _pct(fp_num, fp_den),
        "seller_price_false_positive_denominator": fp_den,
        "seller_negative_item_false_positive_count": neg_item_fp,
        "extra_unmatched_seller_asks_count": extra_unmatched,
        "seller_ask_on_wrong_item_count": wrong_item_asks,
        "false_seller_asks_count": false_asks,
        "reference_promoted_count": ref_promoted,
        "currency_accuracy": _pct(cur_ok, cur_ok + cur_wrong),
        "wear_accuracy": _pct(wear_ok, wear_ok + wear_wrong),
        "linking_accuracy": _pct(link_ok, link_ok + link_wrong),
        "average_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0.0,
        "p50_latency_ms": round(_p50(latencies), 1) if latencies else 0.0,
        "p95_latency_ms": round(_p95(latencies), 1) if latencies else 0.0,
        "average_image_count": round(sum(p.image_count for p in predictions) / n, 2),
        "average_retry_count": round(sum(p.retry_count for p in predictions) / n, 2),
        "blocked_rate": _pct(sum(1 for r in results if not r.predicted_safe), n),
        "fallback_to_text_v2_rate": _pct(fallbacks["text_v2"], n),
        "fallback_to_skipped_rate": _pct(fallbacks["skipped"], n),
        "fallback_counts": fallbacks,
    }


def _compute_readiness(cases: list[EvaluationCase],
                       safe_matrix: dict, parser_stats: dict,
                       crash_count: int,
                       real_data_validation_status: str | None = None) -> str:
    """readiness：NOT_READY / SHADOW_READY / SHADOW_READY_REAL_DATA_PENDING。

    Phase 6.4C1.4：real status 由 caller 先算（含 real coverage），
    此處不得自行重算而漏掉 coverage。
    """
    from alkaid_cs2.evaluation.models import EvaluationSource  # noqa: E402
    eligible = _readiness_eligible(cases)
    if crash_count > 0:
        return READINESS_NOT_READY
    if len(eligible) < 25:
        return READINESS_NOT_READY
    if safe_matrix.get("safe_false_positive_rate", 1.0) > 0.01:
        return READINESS_SHADOW  # 有誤放行 → 最多 SHADOW（6.4B 相容）
    fp_den = parser_stats.get("seller_price_false_positive_denominator", 0)
    if fp_den <= 0:
        return READINESS_SHADOW  # 無法驗證無錯 → 最多 SHADOW（不得 SAFE_PILOT）
    if parser_stats.get("seller_price_false_positive_rate", 1.0) > 0.01:
        return READINESS_SHADOW
    has_real = any(c.source == EvaluationSource.ANONYMIZED_REAL for c in cases)
    if not has_real:
        return READINESS_SHADOW  # 純 synthetic/manual（6.4B 相容）
    # 有 anonymized_real 且 real status != complete → REAL_DATA_PENDING
    if real_data_validation_status != "complete":
        return READINESS_SHADOW_REAL_PENDING
    return READINESS_SHADOW


def _readiness_reasons(cases: list[EvaluationCase], safe_matrix: dict,
                       parser_stats: dict, crash_count: int,
                       intake_ready: bool | None = None) -> list[str]:
    """readiness reason codes（Phase 6.4C1 / 6.4C2-A.2）。"""
    from alkaid_cs2.evaluation.models import (  # noqa: E402
        EvaluationSource, GroundTruthReviewStatus,
    )
    reasons: list[str] = []
    if crash_count > 0:
        reasons.append("crash_present")
    eligible = _readiness_eligible(cases)
    if len(eligible) < 50:
        reasons.append("insufficient_eligible_cases")
    real_total = sum(1 for c in cases
                     if c.source == EvaluationSource.ANONYMIZED_REAL)
    real_double = sum(1 for c in cases
                      if c.source == EvaluationSource.ANONYMIZED_REAL and
                      c.ground_truth_review_status == GroundTruthReviewStatus.DOUBLE_REVIEW)
    if real_total == 0:
        # Phase 6.4C2-A：intake 流程可用 ≠ validation 完成
        reasons.append("no_real_cases_ingested")
        reasons.append("real_analyzer_not_run")
        # Phase 6.4C2-A.2：只有 intake_ready is True 才宣稱流程可用
        if intake_ready is True:
            reasons.append("real_dataset_intake_ready")
    else:
        if real_total < 20:
            reasons.append("insufficient_real_case_count")
        if real_double < 15:
            reasons.append("no_double_reviewed_real_cases")
    if parser_stats.get("seller_price_false_positive_denominator", 0) <= 0:
        reasons.append("seller_fp_denominator_zero")
    if safe_matrix.get("safe_false_positive_rate", 1.0) > 0.01:
        reasons.append("safe_false_positive_above_threshold")
    if not reasons:
        reasons.append("thresholds_met")
    return reasons


def generate_evaluation_report(
    cases: list[EvaluationCase],
    predictions: dict[str, list[EvaluationPrediction]],
    results: dict[str, list[CaseEvaluationResult]],
    *,
    git_commit: str | None = None,
    warnings_seen: list[str] | None = None,
    crash_cases: list[str] | None = None,
    privacy_findings: list | None = None,
    fixture_vs_analyzer: dict | None = None,
    analyzer_coverage: dict | None = None,
    intake_ready: bool | None = None,
) -> dict[str, object]:
    report: dict[str, object] = {
        "dataset": {
            "case_count": len(cases),
            "tags": {},
            "safe_expected_true": sum(1 for c in cases
                                      if c.expected_safe_for_production),
            "raw_safe_expected_true": sum(1 for c in cases
                                          if c.expected_raw_vision_safe is True),
            "raw_safe_expected_false": sum(1 for c in cases
                                           if c.expected_raw_vision_safe is False),
            "raw_safe_expected_none": sum(1 for c in cases
                                          if c.expected_raw_vision_safe is None),
            "multi_image_cases": sum(1 for c in cases if len(c.images) > 1),
            "multi_item_cases": sum(1 for c in cases if len(c.expected_items) > 1),
        },
        "dataset_quality": _dataset_quality(
            cases, privacy_findings or [],
            evaluated_count=len(results.get("vision_production", [])),
            analyzer_coverage=analyzer_coverage),
        "real_data_validation_status": "insufficient",  # 佔位；稍後以含 coverage 版本覆寫
        "intake_ready": intake_ready,  # None=未驗證；只表示 workflow 可用，不表示 production ready
        "fixture_vs_analyzer": fixture_vs_analyzer or {},
        "parsers": {},
        "readiness": READINESS_NOT_READY,
        "crash_cases": crash_cases or [],
        "known_limitations": _known_limitations(cases),
    }
    tag_counter: dict[str, int] = {}
    for c in cases:
        for t in c.tags:
            tag_counter[t] = tag_counter.get(t, 0) + 1
    report["dataset"]["tags"] = dict(sorted(tag_counter.items()))

    crash_cases_list = crash_cases or []
    crash_count = len(crash_cases_list)
    report["crash"] = {
        "cases_executed": len(cases),
        "crash_count": crash_count,
        "crash_rate": _pct(crash_count, len(cases)),
    }

    for name in PARSER_ORDER:
        rs = results.get(name, [])
        ps = predictions.get(name, [])
        entry: dict[str, object] = {"safe": _safe_matrix(rs)}
        stats = _parser_stats(rs, ps)
        if name in ("vision_raw",):
            entry["safe"] = _safe_matrix(rs)
        else:
            entry["safe"] = _safe_matrix(rs)
        # image type / raw conflict 只對 vision_raw 有意義
        if name == "vision_raw":
            kinds = [r.image_kind_correct for r in rs
                     if r.image_kind_correct is not None]
            stats["image_type_accuracy"] = (
                _pct(sum(1 for k in kinds if k), len(kinds)) if kinds else None)
            conflicts_expected = [r for r in rs if r.conflict_expected]
            stats["conflict_detection_rate"] = (
                _pct(sum(1 for r in conflicts_expected if r.conflict_detected),
                     len(conflicts_expected))
                if conflicts_expected else None)
        else:
            stats["image_type_accuracy"] = None
            stats["conflict_detection_rate"] = None
        entry["stats"] = stats
        report["parsers"][name] = entry

    # Phase 6.4C1.4：real status 先算（含 real coverage），readiness 接收
    real_status = _real_data_validation_status(
        cases,
        real_coverage_rate=(analyzer_coverage or {}).get(
            "real_analyzer_coverage_rate"))
    report["real_data_validation_status"] = real_status

    # readiness 以 vision_production 為準
    vis = report["parsers"]["vision_production"]["stats"]
    report["readiness"] = _compute_readiness(
        cases, report["parsers"]["vision_production"]["safe"], vis, crash_count,
        real_data_validation_status=real_status)
    report["readiness_reasons"] = _readiness_reasons(
        cases, report["parsers"]["vision_production"]["safe"], vis, crash_count,
        intake_ready=intake_ready)

    all_w = warnings_seen or []
    wc: dict[str, int] = {}
    for w in all_w:
        key = w.split(":")[0]
        wc[key] = wc.get(key, 0) + 1
    report["top_warning_codes"] = sorted(
        wc.items(), key=lambda kv: (-kv[1], kv[0]))[:10]

    # case-by-case（依 case_id、parser 順序；不含 payload/私人文字）
    case_rows: list[dict[str, object]] = []
    for name in PARSER_ORDER:
        for r in results.get(name, []):
            case_rows.append({
                "case_id": r.case_id, "parser_name": r.parser_name,
                "expected_safe": r.expected_safe, "predicted_safe": r.predicted_safe,
                "item_exact": r.item_exact_matches,
                "item_partial": r.item_partial_matches,
                "item_fp": r.item_false_positives,
                "item_fn": r.item_false_negatives,
                "price_correct": r.seller_price_exact_matches,
                "price_missed": r.seller_price_missed,
                "price_wrong_amount": r.seller_price_wrong_amount,
                "price_wrong_currency": r.seller_price_wrong_currency,
                "false_seller_asks": r.false_seller_asks,
                "ref_promoted": r.reference_promoted_to_seller,
                "seller_ask_wrong_item": r.seller_ask_on_wrong_item,
                "seller_neg_item_fp": r.seller_negative_item_false_positives,
                "extra_unmatched_asks": r.extra_unmatched_seller_asks,
                "seller_price_neg_opps": r.seller_price_negative_opportunities,
                "currency_ok": r.currency_exact_matches,
                "currency_wrong": r.currency_wrong,
                "linking_ok": r.linking_correct,
                "linking_wrong": r.linking_wrong,
                "conflict_expected": r.conflict_expected,
                "conflict_detected": r.conflict_detected,
                "fallback": r.fallback_used,
                "notes": list(r.notes),
            })
    report["case_by_case"] = case_rows

    report["git_commit"] = git_commit
    return report


def write_evaluation_report_json(report: dict[str, object], path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8")


def _md_safe_block(entry: dict[str, object]) -> list[str]:
    safe = entry["safe"]
    lines = ["### Safe gate confusion matrix"]
    lines.append(f"- TP={safe['true_positive']} FP={safe['false_positive']} "
                 f"FN={safe['false_negative']} TN={safe['true_negative']}")
    fp_cases = safe["safe_false_positive_cases"]
    lines.append(f"- **safe false positive cases：{len(fp_cases)}**")
    for fc in fp_cases:
        lines.append(f"  - {fc['case_id']}（{fc['notes']}）")
    return lines


def _md_stats_block(name: str, st: dict[str, object]) -> list[str]:
    lines = ["### Metrics"]
    lines.append(f"- item exact match rate：{_percent_str(st['item_exact_match_rate'])}")
    lines.append(f"- item match recall (exact+partial)：{_percent_str(st['item_match_recall'])}")
    lines.append(f"- item strict recall：{_percent_str(st['item_strict_recall'])}")
    lines.append(f"- item false positives：{st['item_false_positive_count']}")
    lines.append(f"- seller price exact：{_percent_str(st['seller_price_exact_rate'])}")
    lines.append(f"- seller price miss：{_percent_str(st['seller_price_miss_rate'])}")
    lines.append(f"- seller price wrong amount：{_percent_str(st['seller_price_wrong_amount_rate'])}")
    lines.append(f"- seller price wrong currency：{_percent_str(st['seller_price_wrong_currency_rate'])}")
    fp_den = st.get("seller_price_false_positive_denominator", 0)
    fp_num = st.get("seller_negative_item_false_positive_count", 0)
    lines.append(f"- **seller price false positive：{_percent_str(st['seller_price_false_positive_rate'])}"
                 f"（{fp_num} / {fp_den} negative item opportunities）**"
                 f"（negative_item={fp_num}）")
    lines.append(f"- extra unmatched seller asks：{st.get('extra_unmatched_seller_asks_count', 0)}")
    lines.append(f"- seller asks on wrong item：{st.get('seller_ask_on_wrong_item_count', 0)}")
    lines.append(f"- currency accuracy：{_percent_str(st['currency_accuracy'])}")
    lines.append(f"- wear accuracy：{_percent_str(st['wear_accuracy'])}")
    lines.append(f"- linking accuracy：{_percent_str(st['linking_accuracy'])}")
    ita = st.get("image_type_accuracy")
    lines.append(f"- image type accuracy：{_percent_str(ita) if ita is not None else 'N/A'}")
    cdr = st.get("conflict_detection_rate")
    lines.append(f"- raw conflict detection：{_percent_str(cdr) if cdr is not None else 'N/A'}")
    lines.append(f"- fallback to text_v2：{_percent_str(st['fallback_to_text_v2_rate'])}")
    lines.append(f"- fallback to skipped：{_percent_str(st['fallback_to_skipped_rate'])}")
    lines.append(f"- avg latency：{st['average_latency_ms']}ms / "
                 f"P50：{st['p50_latency_ms']}ms / P95：{st['p95_latency_ms']}ms")
    lines.append(f"- avg image count：{st['average_image_count']} / "
                 f"avg retry：{st['average_retry_count']}")
    lines.append(f"- blocked rate：{_percent_str(st['blocked_rate'])}")
    return lines


def write_evaluation_report_markdown(report: dict[str, object], path: str | Path) -> None:
    lines: list[str] = []
    ds = report["dataset"]
    lines.append("# Vision Evaluation Report\n")
    lines.append(f"- case 數：{ds['case_count']}")
    lines.append(f"- safe expected true：{ds['safe_expected_true']} / "
                 f"raw safe true：{ds['raw_safe_expected_true']} / "
                 f"raw safe false：{ds['raw_safe_expected_false']} / "
                 f"raw safe None：{ds['raw_safe_expected_none']}")
    lines.append(f"- multi-image：{ds['multi_image_cases']} / multi-item：{ds['multi_item_cases']}")
    lines.append(f"- git commit：{report.get('git_commit', 'N/A')}")
    lines.append(f"- readiness：**{report['readiness']}**\n")
    reasons = report.get("readiness_reasons") or []
    if reasons:
        lines.append(f"- readiness reasons：{', '.join(reasons)}")

    # Phase 6.4C1.1：Dataset Quality / Source / Review / Privacy / Excluded
    q = report.get("dataset_quality") or {}
    if q:
        lines.append("\n## Dataset Quality")
        lines.append(f"- total_loaded_cases：{q.get('total_loaded_cases', 0)}")
        lines.append(f"- evaluated_cases：{q.get('evaluated_cases', 0)}")
        lines.append(f"- readiness_eligible_cases：{q.get('readiness_eligible_cases', 0)}")
        lines.append(f"- excluded_from_evaluation：{q.get('excluded_from_evaluation', 0)}")
        lines.append(f"- privacy errors：{q.get('privacy_error_count', 0)} / "
                     f"warnings：{q.get('privacy_warning_count', 0)}")
        lines.append(f"- external analyzer cases：{q.get('external_analyzer_cases', 0)} / "
                     f"cached analyzer cases：{q.get('cached_analyzer_cases', 0)}")
    lines.append("\n## Source distribution")
    lines.append(f"- synthetic：{q.get('synthetic_cases', 0)} / "
                 f"anonymized_real：{q.get('anonymized_real_cases', 0)} / "
                 f"manual_fixture：{q.get('manual_fixture_cases', 0)} / "
                 f"adversarial：{q.get('adversarial_cases', 0)}")
    lines.append("\n## Review distribution")
    lines.append(f"- double_review：{q.get('double_reviewed_cases', 0)} / "
                 f"single_review：{q.get('single_reviewed_cases', 0)} / "
                 f"disputed：{q.get('disputed_cases', 0)}")
    lines.append(f"- real data validation status：{report.get('real_data_validation_status', 'N/A')}\n")

    # Phase 6.4C1.2：Fixture vs Analyzer 區塊（與 JSON 一致）
    fva = report.get("fixture_vs_analyzer") or {}
    if fva:
        lines.append("## Fixture vs Analyzer")
        lines.append(f"- cache lookup：{fva.get('cache_lookup_count', 0)} / "
                     f"hit：{fva.get('cache_hit_count', 0)} / "
                     f"miss：{fva.get('cache_miss_count', 0)}")
        lines.append(f"- cached cases：{q.get('cached_analyzer_cases', 0)} / "
                     f"cached images：{fva.get('images_with_cache', 0)}")
        lines.append(f"- external cases：{q.get('external_analyzer_cases', 0)} / "
                     f"external images：{q.get('external_analyzer_images', 0)}")
        lines.append(f"- analyzer coverage：{q.get('analyzer_coverage_rate', 0.0):.2%}"
                     f"（{q.get('analyzer_eligible_images', 0)} eligible images）")
        lines.append(f"- evaluated analyzer eligible images：{q.get('analyzer_eligible_images', 0)} / "
                     f"real analyzer eligible images：{q.get('real_analyzer_eligible_images', 0)}")
        lines.append(f"- real cache/analyzer coverage："
                     f"{q.get('real_analyzer_coverage_rate', 0.0):.2%}"
                     f"（cached {q.get('real_cached_analyzer_images', 0)} / "
                     f"external {q.get('real_external_analyzer_images', 0)}）")
        lines.append("> ⚠️ 注意：fixture-mirrored cache accuracy **不代表真實模型準確率**"
                     "（cache 內容 = 人工 fixture payload 鏡像）")
        lines.append(f"- images compared：{fva.get('images_compared', 0)}")
        lines.append(f"- image kind accuracy：{fva.get('image_kind_accuracy', 0.0):.2%} / "
                     f"item count：{fva.get('item_count_accuracy', 0.0):.2%} / "
                     f"item exact：{fva.get('item_exact_rate', 0.0):.2%} / "
                     f"price exact：{fva.get('price_exact_rate', 0.0):.2%} / "
                     f"currency：{fva.get('currency_accuracy', 0.0):.2%}")
        lines.append(f"- skipped（no fixture payload）：{fva.get('comparison_skipped_no_fixture_payload', 0)} / "
                     f"skipped（no analyzer payload）：{fva.get('comparison_skipped_no_analyzer_payload', 0)}")
        dis = fva.get("disagreement_cases", [])
        if dis:
            lines.append(f"- disagreement cases：{', '.join(dis)}")
        else:
            lines.append("- disagreement cases：無")
        lines.append("")

    for name in PARSER_ORDER:
        entry = report["parsers"][name]
        st = entry["stats"]
        lines.append(f"## {name}\n")
        lines.extend(_md_safe_block(entry))
        lines.extend(_md_stats_block(name, st))
        lines.append("")

    lines.append("## Top warning codes")
    for code, cnt in report.get("top_warning_codes", []):
        lines.append(f"- {code}：{cnt}")
    lines.append("\n## Crash")
    crash = report.get("crash", {})
    lines.append(f"- cases_executed={crash.get('cases_executed')} "
                 f"crash_count={crash.get('crash_count')} "
                 f"crash_rate={crash.get('crash_rate')}")
    lines.append("\n## Known limitations")
    for lim in report.get("known_limitations", []):
        lines.append(f"- {lim}")
    lines.append("\n## Readiness recommendation")
    lines.append(f"**{report['readiness']}**\n")
    Path(path).write_text("\n".join(lines), encoding="utf-8")
