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

PARSER_ORDER = ("legacy", "text_v2", "vision_raw", "vision_production")

KNOWN_LIMITATIONS = [
    "all_cases_synthetic",
    "vision_payloads_are_fixture_outputs",
    "offline_legacy_is_not_deepseek_legacy",
    "latency_is_local_runtime_metadata",
    "image_type_accuracy_is_fixture_biased",
]


def _pct(num: int, den: int) -> float:
    return num / den if den else 0.0


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


def _compute_readiness(cases: list[EvaluationCase], safe_matrix: dict[str, object],
                       parser_stats: dict[str, object],
                       crash_count: int) -> str:
    n = len(cases)
    if n < 25 or crash_count > 0:
        return READINESS_NOT_READY
    if n < 50:
        return READINESS_SHADOW  # <50 最多 SHADOW_READY（不得 SAFE_PILOT_CANDIDATE）
    if safe_matrix["safe_false_positive_rate"] > 0.01:
        return READINESS_SHADOW
    # seller FP：denominator 必須 > 0 且 rate <= 1%
    fp_den = parser_stats.get("seller_price_false_positive_denominator", 0)
    if fp_den <= 0:
        return READINESS_SHADOW
    if parser_stats.get("seller_price_false_positive_rate", 1.0) > 0.01:
        return READINESS_SHADOW
    if parser_stats.get("currency_accuracy", 0.0) < 0.99:
        return READINESS_SHADOW
    if parser_stats.get("item_exact_match_rate", 0.0) < 0.90:
        return READINESS_SHADOW
    if parser_stats.get("item_match_recall", 0.0) < 0.95:
        return READINESS_SHADOW
    if parser_stats.get("linking_accuracy", 0.0) < 0.95:
        return READINESS_SHADOW
    return READINESS_SAFE_PILOT


def generate_evaluation_report(
    cases: list[EvaluationCase],
    predictions: dict[str, list[EvaluationPrediction]],
    results: dict[str, list[CaseEvaluationResult]],
    *,
    git_commit: str | None = None,
    warnings_seen: list[str] | None = None,
    crash_cases: list[str] | None = None,
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
        "parsers": {},
        "readiness": READINESS_NOT_READY,
        "crash_cases": crash_cases or [],
        "known_limitations": list(KNOWN_LIMITATIONS),
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

    # readiness 以 vision_production 為準
    vis = report["parsers"]["vision_production"]["stats"]
    report["readiness"] = _compute_readiness(
        cases, report["parsers"]["vision_production"]["safe"], vis, crash_count)

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
