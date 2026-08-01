#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
compare_real_case_reviews.py — Review Diff CLI（Phase 6.4C2-A）

- 比較 reviewer_a.json / reviewer_b.json
- 輸出 per-field 結果 + 決策（double_review / disputed / single_review）
- 不打印 raw_text 或敏感內容

exit code：0 成功 / 1 workflow 錯誤 / 2 schema/validation 錯誤
"""

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.review_workflow import (
    compute_review_decision,
    load_reviews,
)

DEFAULT_OUTPUT = Path("local_data/real_intake/reviews")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Compare Reviewer A/B Annotations")
    ap.add_argument("--reviews-dir", default=None)
    ap.add_argument("--output", default=None)
    ap.add_argument("--json", dest="as_json", action="store_true")
    args = ap.parse_args(argv)

    rev_dir = Path(args.reviews_dir) if args.reviews_dir else DEFAULT_OUTPUT
    try:
        a, b = load_reviews(rev_dir)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        # Phase 6.4C2-A.4：schema/JSON/檔案錯誤 → exit 2（不 traceback）
        print(f"[compare] ❌ review 驗證失敗：{exc}", file=sys.stderr)
        return 2
    if a is None or b is None:
        print("[compare] ⚠️ 缺 reviewer 標註（single_review）", file=sys.stderr)
        return 1

    try:
        decision = compute_review_decision(rev_dir)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"[compare] ❌ review 驗證失敗：{exc}", file=sys.stderr)
        return 2
    if args.as_json:
        out = {"decision": decision["decision"],
               "field_results": decision["field_results"],
               "disputed_fields": decision["disputed_fields"]}
        if args.output:
            try:
                Path(args.output).write_text(
                    json.dumps(out, ensure_ascii=False, indent=2),
                    encoding="utf-8")
            except (OSError, TypeError, ValueError) as exc:
                # Phase 6.4C2-A.5：output 寫檔失敗受控（exit 2、不 traceback）
                print(f"[compare] ❌ output 寫入失敗：{exc}", file=sys.stderr)
                return 2
            print(f"[compare] ✅ 寫入 {args.output}")
        else:
            print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    print(f"[compare] 決策：{decision['decision']}")
    for f, r in decision["field_results"].items():
        marker = "⚠️" if r in ("mismatch", "missing_on_a", "missing_on_b") else "✅"
        print(f"  {marker} {f}: {r}")
    if decision["disputed_fields"]:
        print(f"[compare] disputed fields: {', '.join(decision['disputed_fields'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
