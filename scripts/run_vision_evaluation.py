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
                   legacy_parser=None, git_commit=None):
    """執行評估，回傳 (report, exit_code)。"""
    try:
        cases = load_evaluation_directory(fixtures_dir)
    except (ValueError, TypeError, KeyError) as exc:
        print(f"[eval] ❌ dataset/schema 錯誤：{exc}")
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

    parser = legacy_parser or _offline_legacy_parser
    full_dict, pattern_dict, weapon_map = _load_v2_resources()

    parser_names = ("legacy", "text_v2", "vision_raw", "vision_production")
    predictions: dict[str, list] = {n: [] for n in parser_names}
    results: dict[str, list] = {n: [] for n in parser_names}
    crash: list[str] = []
    all_warnings: list[str] = []

    for i, case in enumerate(cases, 1):
        print(f"[eval] {i}/{len(cases)} {case.case_id} ...")
        try:
            ev = evaluate_case(
                case, full_dict=full_dict, pattern_dict=pattern_dict,
                weapon_map=weapon_map, legacy_parser=parser)
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

    report = generate_evaluation_report(
        cases, predictions, results,
        git_commit=git_commit or _git_commit(),
        warnings_seen=all_warnings, crash_cases=crash)

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    if "json" in formats:
        write_evaluation_report_json(report, out / "phase6-4-baseline.json")
    if "md" in formats:
        write_evaluation_report_markdown(report, out / "phase6-4-baseline.md")
    print(f"[eval] ✅ 報告完成：readiness={report['readiness']} "
          f"cases={len(cases)} crash={len(crash)}")
    return report, (1 if crash else 0)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Vision 離線評估")
    ap.add_argument("--fixtures", default="tests/fixtures/evaluation")
    ap.add_argument("--output", default="tests/evaluation/reports")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--fail-fast", action="store_true")
    ap.add_argument("--format", choices=["json", "md", "both"], default="both")
    args = ap.parse_args(argv)

    formats = ("json", "md") if args.format == "both" else (args.format,)
    report, code = run_evaluation(
        args.fixtures, args.output, limit=args.limit, tag=args.tag,
        case_id=args.case_id, fail_fast=args.fail_fast, formats=formats)
    return code if report is not None else 2


if __name__ == "__main__":
    sys.exit(main())
