#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
adjudicate_real_case.py — Adjudication CLI（Phase 6.4C2-A）

- 只有 disputed 可進 adjudication
- 由 reviewer_c / adjudicator 產生決議（不得真實姓名）
- 保存 decision reason、input hash、final GT hash
- 不刪除原 reviewer A/B 標註
- 不得參考 parser/analyzer 輸出

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

from alkaid_cs2.evaluation.review_workflow import (  # noqa: E402
    AtomicWriteError,
    adjudicate_disputed,
    compute_review_decision,
)

DEFAULT_OUTPUT = Path("local_data/real_intake/reviews")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Adjudicate Disputed Review")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--adjudicator", required=True)
    ap.add_argument("--reason", required=True, help="decision reason")
    ap.add_argument("--final-gt-json", required=True,
                    help="最終 Ground Truth JSON（必填，Phase 6.4C2-A.2）")
    ap.add_argument("--reviews-dir", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    rev_dir = Path(args.reviews_dir) if args.reviews_dir else DEFAULT_OUTPUT
    try:
        current = compute_review_decision(rev_dir)
    except (ValueError, json.JSONDecodeError):
        # Phase 6.4C2-A.7：固定錯誤碼（不回顯 case_id/reviewer_id/annotations）
        print("[adjudicate] ❌ review_validation_failed", file=sys.stderr)
        return 2
    except OSError:
        print("[adjudicate] ❌ review_read_failed", file=sys.stderr)
        return 2
    if current["decision"] != "disputed":
        print("[adjudicate] ❌ not_disputed", file=sys.stderr)
        return 1
    if args.case_id != current.get("case_id"):
        print("[adjudicate] ❌ case_id_mismatch", file=sys.stderr)
        return 2

    final_gt = None
    try:
        final_gt = json.loads(Path(args.final_gt_json).read_text(
            encoding="utf-8"))
    except json.JSONDecodeError:
        print("[adjudicate] ❌ final_gt_invalid_json", file=sys.stderr)
        return 2
    except OSError:
        print("[adjudicate] ❌ final_gt_read_failed", file=sys.stderr)
        return 2

    # Phase 6.4C2-A.3：dry-run 與正式模式一致（完整驗證，只跳過寫檔）
    try:
        record = adjudicate_disputed(
            rev_dir, adjudicator=args.adjudicator,
            decision_reason=args.reason, case_id=args.case_id,
            final_ground_truth=final_gt, write_files=not args.dry_run)
    except AtomicWriteError:
        # Phase 6.4C2-A.6：固定錯誤碼（不印 str(exc)/敏感內容/路徑）
        print("[adjudicate] ❌ atomic_write_failed", file=sys.stderr)
        return 2
    except (ValueError, TypeError) as exc:
        # Phase 6.4C2-A.6：schema 錯誤只輸出固定碼（不回顯 annotations 原文）
        print("[adjudicate] ❌ validation_failed", file=sys.stderr)
        return 2
    except OSError:
        print("[adjudicate] ❌ atomic_write_failed", file=sys.stderr)
        return 2
    if args.dry_run:
        print("[adjudicate] dry-run：完整驗證通過（不寫檔）")
        return 0
    print("[adjudicate] ✅ 決議完成：double_review（adjudication.json + "
          "final_ground_truth.json 已原子寫入）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
