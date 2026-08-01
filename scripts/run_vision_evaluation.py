#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_vision_evaluation.py — 離線 Vision 評估 runner（Phase 6.4A-6.4B）

用法：
  python scripts/run_vision_evaluation.py \\
      --fixtures tests/fixtures/evaluation \\
      --output tests/evaluation/reports \\
      --format both

exit code：0=完成 1=案例執行錯誤 2=dataset/schema 錯誤
不呼叫 Facebook / Vision API / BUFF / DeepSeek。
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory  # noqa: E402
from alkaid_cs2.evaluation.evaluator import evaluate_case  # noqa: E402
from alkaid_cs2.evaluation.models import (  # noqa: E402
    EvaluationSource, GroundTruthReviewStatus,
)
from alkaid_cs2.evaluation.report import (  # noqa: E402
    generate_evaluation_report,
    write_evaluation_report_json,
    write_evaluation_report_markdown,
)
from alkaid_cs2.evaluation.scoring import score_case  # noqa: E402


def _offline_legacy_parser(text: str) -> dict | None:
    """離線 legacy 近似（**非正式 legacy**）：僅供評估，不呼叫 DeepSeek。

    正規 legacy（extract_skin_info）需 DeepSeek；此處以極簡規則替代，
    報告 known_limitations 註明差異。
    """
    if not text:
        return None
    m = re.search(
        r"售\s*(.+?)\s*(?:久經沙場|久经沙场|嶄新出廠|崭新出厂|略有磨損|略有磨损|"
        r"戰痕累累|战痕累累|破損不堪|破损不堪)?\s*(?:算|賣)?\s*(\d+)\s*(TWD|RMB)?",
        text)
    if not m:
        return None
    return {
        "market_hash_name": m.group(1).strip(),
        "seller_price": int(m.group(2)),
        "currency": m.group(3) or "TWD",
        "blocked": False,
        "wear": "",
    }


def _git_commit() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True,
                             text=True, timeout=10, cwd=Path(__file__).parent.parent)
        return out.stdout.strip() or None
    except Exception:
        return None


def _load_v2_resources():
    """從 analyze_arbitrage 讀取正式字典（唯讀，不修改）。"""
    import analyze_arbitrage as aa
    full_dict, pattern_dict = aa._load_v2_dicts()
    weapon_map = dict(aa._V2_WEAPON_MAP)
    return full_dict, pattern_dict, weapon_map


def run_evaluation(fixtures_dir, output_dir, *, limit=None, tag=None,
                   case_id=None, fail_fast=False, formats=("json", "md"),
                   legacy_parser=None, git_commit=None,
                   real_fixtures=None, adversarial_fixtures=None,
                   analyzer_cache=None, include_single_review=False,
                   include_disputed=False, compare_analyzer=False,
                   report_filename="phase6-4-baseline.json"):
    """執行評估，回傳 (report, exit_code)。"""
    try:
        cases = load_evaluation_directory(fixtures_dir)
    except (ValueError, TypeError, KeyError) as exc:
        print(f"[eval] ❌ dataset/schema 錯誤：{exc}")
        return None, 2

    # Phase 6.4C1.1：全部案例進 dataset（含 single/disputed），
    # 但 excluded 預設不跑 parser（除非 include_single_review/include_disputed）
    analyzer_payloads: dict[str, dict] = {}
    cache_stats = {"lookup": 0, "hits": 0, "misses": 0}
    if real_fixtures:
        try:
            real_cases = load_evaluation_directory(real_fixtures)
        except (ValueError, TypeError, KeyError) as exc:
            print(f"[eval] ❌ real dataset/schema 錯誤：{exc}")
            return None, 2
        cases.extend(real_cases)
    if adversarial_fixtures:
        try:
            cases.extend(load_evaluation_directory(adversarial_fixtures))
        except (ValueError, TypeError, KeyError) as exc:
            print(f"[eval] ❌ adversarial dataset/schema 錯誤：{exc}")
            return None, 2

    if tag:
        cases = [c for c in cases if tag in c.tags]
    if case_id:
        cases = [c for c in cases if c.case_id == case_id]
    if limit is not None:
        cases = cases[:limit]
    if not cases:
        print("[eval] ⚠️ 無案例（檢查 filter）")
        return None, 2

    # Phase 6.4C1.3/6.4C1.4：evaluated population 統一（單一 helper）
    # （cache lookup、fixture comparison、parser loop、coverage 全部用同一份）
    evaluated_cases = [c for c in cases
                       if should_evaluate_case(c, include_single_review,
                                               include_disputed)]

    # Phase 6.4C1：analyzer cache 載入（離線；offline miss 不呼叫外部）
    if analyzer_cache:
        from alkaid_cs2.evaluation.vision_analyzer_runner import (  # noqa: E402
            cache_lookup, compute_image_hash,
        )
        from alkaid_cs2.evaluation.vision_analyzer_runner import (  # noqa: E402
            AnalyzerRunConfig,
        )
        cfg = AnalyzerRunConfig(model_name="gemini-2.5-flash",
                                prompt_version="cs2-vision-v1")
        cache_dir = Path(analyzer_cache)
        for c in evaluated_cases:
            for img in c.images:
                # fixture 無原始 bytes：以 image_url 內容產生穩定 hash（離線）
                fake = img.image_url.encode("utf-8")
                h = compute_image_hash(fake)
                cache_stats["lookup"] += 1
                payload = cache_lookup(cache_dir, h, cfg.model_name,
                                       cfg.prompt_version)
                if payload is not None:
                    analyzer_payloads.setdefault(c.case_id, {})[img.image_index] = payload
                    cache_stats["hits"] += 1
                else:
                    cache_stats["misses"] += 1

    parser = legacy_parser or _offline_legacy_parser
    full_dict, pattern_dict, weapon_map = _load_v2_resources()

    parser_names = ("legacy", "text_v2", "vision_raw", "vision_production")
    predictions: dict[str, list] = {n: [] for n in parser_names}
    results: dict[str, list] = {n: [] for n in parser_names}
    crash: list[str] = []
    all_warnings: list[str] = []

    for i, case in enumerate(cases, 1):
        # Phase 6.4C1.4：與 evaluated_cases 同一 helper（不得複製條件）
        if not should_evaluate_case(case, include_single_review,
                                    include_disputed):
            print(f"[eval] {i}/{len(cases)} {case.case_id} (excluded, skip)")
            continue
        print(f"[eval] {i}/{len(cases)} {case.case_id} ...")
        try:
            ev = evaluate_case(
                case, full_dict=full_dict, pattern_dict=pattern_dict,
                weapon_map=weapon_map, legacy_parser=parser,
                real_analyzer_payloads=analyzer_payloads.get(case.case_id))
            raw_merge = ev.get("raw_vision_merge")
            for name in parser_names:
                pred = ev[name]
                predictions[name].append(pred)
                if name == "vision_raw":
                    # raw merge 只可用於 vision_raw（圖片分類/raw conflict）
                    results[name].append(score_case(
                        case, name, pred, raw_merge=raw_merge,
                        expected_safe=case.expected_raw_vision_safe))
                else:
                    results[name].append(score_case(
                        case, name, pred, raw_merge=None,
                        expected_safe=case.expected_safe_for_production))
            all_warnings.extend(ev["vision_production"].warnings)
        except Exception as exc:  # 單案例錯誤記錄後繼續（fail_fast 才停）
            msg = f"{case.case_id}:{type(exc).__name__}:{str(exc)[:150]}"
            print(f"[eval] ⚠️ {msg}")
            if fail_fast:
                print(f"[eval] ❌ fail-fast：{msg}")
                return None, 1
            crash.append(msg)

    # Phase 6.4C1：privacy 掃描 + fixture vs analyzer 對比
    from alkaid_cs2.evaluation.privacy import (  # noqa: E402
        scan_fixture_for_sensitive_data,
    )
    privacy_findings = []
    for c in cases:
        privacy_findings.extend(scan_fixture_for_sensitive_data(c))

    fixture_vs_analyzer = None
    if compare_analyzer and analyzer_payloads:
        # Phase 6.4C1.4：comparison 只收 evaluated_cases（與 cache lookup 同母體）
        fixture_vs_analyzer = _compare_fixture_analyzer(
            evaluated_cases, analyzer_payloads, cache_stats)

    # Phase 6.4C1.2：analyzer coverage（case 以 unique case_id、image 以 image_index 計）
    # Phase 6.4C1.3：real coverage 獨立（只統計 anonymized_real）
    from alkaid_cs2.evaluation.models import EvaluationSource  # noqa: E402
    real_cases = [c for c in evaluated_cases
                  if c.source == EvaluationSource.ANONYMIZED_REAL]
    real_hits = sum(1 for c in real_cases
                    for img in c.images
                    if analyzer_payloads.get(c.case_id, {}).get(img.image_index))
    analyzer_coverage = {
        "external_analyzer_cases": 0,
        "external_analyzer_images": 0,
        "cached_analyzer_cases": len({c.case_id for c in evaluated_cases
                                      if analyzer_payloads.get(c.case_id)}),
        "cached_analyzer_images": cache_stats.get("hits", 0),
        "analyzer_eligible_images": sum(len(c.images) for c in evaluated_cases),
        "real_analyzer_eligible_images": sum(len(c.images) for c in real_cases),
        "real_cached_analyzer_images": real_hits,
        "real_external_analyzer_images": 0,
        "real_analyzer_coverage_rate": round(real_hits / max(1, sum(len(c.images) for c in real_cases)), 4),
    }

    report = generate_evaluation_report(
        cases, predictions, results,
        git_commit=git_commit or _git_commit(),
        warnings_seen=all_warnings, crash_cases=crash,
        privacy_findings=privacy_findings,
        fixture_vs_analyzer=fixture_vs_analyzer,
        analyzer_coverage=analyzer_coverage)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if "json" in formats:
        write_evaluation_report_json(report, out / report_filename)
    if "md" in formats:
        write_evaluation_report_markdown(report, out / report_filename.replace(".json", ".md"))
    print(f"[eval] ✅ 報告完成：readiness={report['readiness']} "
          f"cases={len(cases)} crash={len(crash)}")
    return report, (1 if crash else 0)


def should_evaluate_case(case, include_single_review: bool,
                         include_disputed: bool) -> bool:
    """單一 inclusion 規則（Phase 6.4C1.4）。

    - SINGLE_REVIEW：只有 include_single_review=True 才納入
    - DISPUTED：只有 include_disputed=True 才納入
    - 其他 excluded_from_readiness：預設不納入（不得因任一 flag 自動納入）
    - 一般案例：納入
    """
    from alkaid_cs2.evaluation.models import GroundTruthReviewStatus  # noqa: E402
    status = case.ground_truth_review_status
    if status == GroundTruthReviewStatus.SINGLE_REVIEW:
        return include_single_review
    if status == GroundTruthReviewStatus.DISPUTED:
        return include_disputed
    if case.excluded_from_readiness:
        return False
    return True


def _compare_fixture_analyzer(cases, analyzer_payloads: dict,
                              cache_stats: dict) -> dict:
    """fixture payload vs analyzer cache payload 對比（Phase 6.4C1.1）。

    - cache miss 不算 analyzer failure
    - fixture payload 缺失不算 analyzer failure
    """
    from alkaid_cs2.evaluation.vision_analyzer_runner import (  # noqa: E402
        compare_fixture_and_analyzer_payload,
    )
    compared = images = 0
    kind_ok = item_count_ok = item_exact_total = price_ok = cur_ok = 0
    item_den = price_den = cur_den = 0
    skipped_no_fixture = skipped_no_analyzer = 0
    disagreements: list[str] = []
    for case in cases:
        payloads = analyzer_payloads.get(case.case_id)
        for img in case.images:
            a_payload = payloads.get(img.image_index) if payloads else None
            if a_payload is None:
                skipped_no_analyzer += 1
                continue
            f_payload = img.vision_payload
            if f_payload is None:
                skipped_no_fixture += 1
                continue
            compared += 1
            images += 1
            c = compare_fixture_and_analyzer_payload(f_payload, a_payload)
            kind_ok += int(c.image_kind_match)
            item_count_ok += int(c.item_count_match)
            item_exact_total += c.exact_name_matches
            item_den += len(f_payload.get("items") or [])
            price_ok += int(c.price_match)
            price_den += 1
            cur_ok += int(c.currency_match)
            cur_den += 1
            if not (c.image_kind_match and c.item_count_match and
                    c.price_match and c.currency_match):
                disagreements.append(case.case_id)
    disagreements = sorted(set(disagreements))
    lookup = cache_stats.get("lookup", 0)
    hits = cache_stats.get("hits", 0)
    misses = cache_stats.get("misses", 0)
    return {
        "cases_compared": len({c.case_id for c in cases
                               if analyzer_payloads.get(c.case_id)}),
        "images_compared": images,
        "image_kind_accuracy": round(kind_ok / compared, 4) if compared else 0.0,
        "item_count_accuracy": round(item_count_ok / compared, 4) if compared else 0.0,
        "item_exact_rate": round(item_exact_total / item_den, 4) if item_den else 0.0,
        "price_exact_rate": round(price_ok / price_den, 4) if price_den else 0.0,
        "currency_accuracy": round(cur_ok / cur_den, 4) if cur_den else 0.0,
        "cache_lookup_count": lookup,
        "cache_hit_count": hits,
        "cache_miss_count": misses,
        "images_with_cache": hits,
        "images_without_cache": misses,
        "analyzer_success_count": 0,
        "analyzer_failure_count": 0,
        "analyzer_failure_rate": 0.0,
        "comparison_skipped_no_fixture_payload": skipped_no_fixture,
        "comparison_skipped_no_analyzer_payload": skipped_no_analyzer,
        "cache_hit_rate": round(hits / max(1, lookup), 4) if lookup else 0.0,
        "disagreement_cases": disagreements,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Vision 離線評估")
    ap.add_argument("--fixtures", default="tests/fixtures/evaluation")
    ap.add_argument("--output", default="tests/evaluation/reports")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--format", choices=["json", "md", "both"], default="both")
    # Phase 6.4C1
    ap.add_argument("--real-fixtures", default=None)
    ap.add_argument("--adversarial-fixtures", default=None)
    ap.add_argument("--analyzer-cache", default=None)
    ap.add_argument("--include-single-review", action="store_true")
    ap.add_argument("--include-disputed", action="store_true")
    ap.add_argument("--compare-analyzer-cache", action="store_true")
    ap.add_argument("--report-filename", default=None,
                    help="輸出檔名（不含副檔名；預設 phase6-4-baseline / 6.4C1 自動）")
    args = ap.parse_args(argv)

    formats = ("json", "md") if args.format == "both" else (args.format,)
    has_real_adv = bool(args.real_fixtures or args.adversarial_fixtures)
    if args.report_filename:
        rfn = f"{args.report_filename}.json"
    else:
        rfn = "phase6-4c1-baseline.json" if has_real_adv else "phase6-4-baseline.json"
    report, code = run_evaluation(
        args.fixtures, args.output, limit=args.limit, tag=args.tag,
        case_id=args.case_id, fail_fast=args.fail_fast, formats=formats,
        real_fixtures=args.real_fixtures,
        adversarial_fixtures=args.adversarial_fixtures,
        analyzer_cache=args.analyzer_cache,
        include_single_review=args.include_single_review,
        include_disputed=args.include_disputed,
        compare_analyzer=args.compare_analyzer_cache,
        report_filename=rfn)
    return code if report is not None else 2


if __name__ == "__main__":
    sys.exit(main())
