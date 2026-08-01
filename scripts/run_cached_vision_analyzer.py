#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
run_cached_vision_analyzer.py — 真實 Vision Analyzer 離線 runner（Phase 6.4C1）

python scripts/run_cached_vision_analyzer.py \
  --fixtures tests/fixtures/evaluation_real \
  --cache tests/fixtures/vision_analyzer_cache \
  --model <model> --prompt-version <version> \
  --offline [--case-id X] [--limit N] [--dry-run]

模式：
- 預設 offline：只讀 cache，miss 不呼叫外部服務
- 外部呼叫需同時 --allow-external-analyzer + EVALUATION_ALLOW_EXTERNAL_ANALYZER=1
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alkaid_cs2.evaluation.dataset_loader import load_evaluation_directory
from alkaid_cs2.evaluation.vision_analyzer_runner import (
    AnalyzerRunConfig, run_analyzer_for_case,
)


def _image_loader_from_urls(case, image_index: int) -> bytes | None:
    """離線 image loader：fixture 無原始 bytes，用 URL 產生穩定 hash。

    正式使用時應替換為真實圖片載入（FB CDN 下載等），但不得 commit bytes。
    """
    for img in case.images:
        if img.image_index == image_index:
            return img.image_url.encode("utf-8") or None
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description="Cached Vision Analyzer Runner")
    ap.add_argument("--fixtures", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--model", default="gemini-2.5-flash")
    ap.add_argument("--prompt-version", default="cs2-vision-v1")
    ap.add_argument("--case-id", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--offline", action="store_true",
                    help="只讀 cache（預設）")
    ap.add_argument("--allow-external-analyzer", action="store_true")
    args = ap.parse_args()

    # Phase 6.4C1.1：--offline 與 --allow-external-analyzer 互斥
    if args.offline and args.allow_external_analyzer:
        print("錯誤: --offline 與 --allow-external-analyzer 互斥（exit 2）",
              file=sys.stderr)
        return 2

    # 外部呼叫 gate：flag + env 缺一不可
    allow_external = args.allow_external_analyzer and \
        os.environ.get("EVALUATION_ALLOW_EXTERNAL_ANALYZER") == "1"
    if args.allow_external_analyzer and not allow_external:
        print("錯誤: --allow-external-analyzer 需要 EVALUATION_ALLOW_EXTERNAL_ANALYZER=1",
              file=sys.stderr)
        return 2

    # Phase 6.4C1.1：沒有真實 analyzer adapter 時，外部模式直接拒絕
    # （不得使用 analyzer=lambda b, p: None 假 adapter）
    real_analyzer = None
    real_loader = None
    if allow_external:
        try:
            from alkaid_cs2.evaluation.vision_analyzer_real import (  # noqa: F401
                run_real_analyzer, load_real_image,
            )
            real_analyzer = run_real_analyzer
            real_loader = load_real_image
        except ImportError:
            print("錯誤: 未設定真實 analyzer adapter / image loader"
                  "（alkaid_cs2/evaluation/vision_analyzer_real.py 不存在）；"
                  "外部模式拒絕執行（exit 2）", file=sys.stderr)
            return 2

    # Phase 6.4C1.2：離線模式仍可用 URL bytes 計算 placeholder cache key
    image_loader = real_loader if (allow_external and real_loader is not None) \
        else _image_loader_from_urls

    fixtures_dir = Path(args.fixtures)
    cache_dir = Path(args.cache)
    if not fixtures_dir.is_dir():
        print(f"錯誤: 目錄不存在 {fixtures_dir}", file=sys.stderr)
        return 2

    cases = load_evaluation_directory(fixtures_dir)
    if args.case_id:
        cases = [c for c in cases if c.case_id == args.case_id]
        if not cases:
            print(f"錯誤: 找不到 case {args.case_id}", file=sys.stderr)
            return 2
    if args.limit:
        cases = cases[: args.limit]

    config = AnalyzerRunConfig(
        model_name=args.model,
        prompt_version=args.prompt_version,
        use_cache=True,
        write_cache=not args.dry_run and allow_external and real_analyzer is not None,
    )

    hits = misses = failures = 0
    for case in cases:
        results = run_analyzer_for_case(
            case, image_loader, analyzer=real_analyzer,
            config=config, cache_dir=cache_dir, allow_external=allow_external)
        for r in results:
            if r.cached:
                hits += 1
            elif r.success:
                misses += 1  # 外部執行成功（未命中 cache）
            else:
                failures += 1
                if r.error_code == "cache_miss_offline":
                    print(f"[offline-miss] {case.case_id} img{r.image_index}")
                else:
                    print(f"[fail] {case.case_id} img{r.image_index}: {r.error_code}")
        print(f"{case.case_id}: images={len(results)} "
              f"hits={sum(1 for r in results if r.cached)}")

    print(f"\n總結: cases={len(cases)} cache_hits={hits} "
          f"external={misses} failures={failures}")
    print(f"模式: {'offline（只讀 cache）' if not allow_external else '外部 analyzer 已啟用'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
