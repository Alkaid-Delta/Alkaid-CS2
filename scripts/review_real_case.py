#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
review_real_case.py — Reviewer 標註 CLI（Phase 6.4C2-A）

- 寫 reviewer_a.json / reviewer_b.json（獨立，不得互相覆寫）
- reviewer identity 限 reviewer_a/b/c（不得真實姓名）
- 標註內容由 --annotations-json 提供（reviewer 手動產生）

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

from alkaid_cs2.evaluation.review_workflow import write_review

DEFAULT_OUTPUT = Path("local_data/real_intake/reviews")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Review Real Case (single reviewer)")
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--reviewer", required=True,
                    choices=sorted({"reviewer_a", "reviewer_b", "reviewer_c"}))
    ap.add_argument("--annotations-json", required=True,
                    help="標註 JSON 檔案路徑（reviewer 手動產生）")
    ap.add_argument("--output", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.annotations_json)
    if not p.exists():
        print(f"[review] ❌ annotations 不存在：{p}", file=sys.stderr)
        return 2
    try:
        annotations = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[review] ❌ annotations 無法解析：{exc}", file=sys.stderr)
        return 2
    if not isinstance(annotations, dict):
        print("[review] ❌ annotations 必須是 JSON object", file=sys.stderr)
        return 2

    if args.dry_run:
        # Phase 6.4C2-A.4：dry-run 與正式模式相同驗證（只跳過寫入）
        from alkaid_cs2.evaluation.review_schema import (  # noqa: E402
            validate_annotations,
        )
        from alkaid_cs2.evaluation.intake_models import (  # noqa: E402
            validate_reviewer_id,
        )
        schema_errors: list[str] = []
        if not validate_reviewer_id(args.reviewer):
            schema_errors.append(f"reviewer_id_invalid:{args.reviewer!r}")
        if not args.case_id.strip():
            schema_errors.append("case_id_missing")
        schema_errors.extend(validate_annotations(annotations))
        if schema_errors:
            print(f"[review] ❌ dry-run 驗證失敗：{', '.join(schema_errors)}",
                  file=sys.stderr)
            return 2
        print(f"[review] dry-run：{args.reviewer} 標註驗證通過"
              f"（{len(annotations)} 欄位，不寫檔）")
        return 0

    try:
        out_dir = Path(args.output) if args.output else DEFAULT_OUTPUT
        path = write_review(out_dir, args.reviewer, annotations, args.case_id)
    except (ValueError, TypeError, OSError) as exc:
        # Phase 6.4C2-A.5：constructor/write 錯誤受控（exit 2、不 traceback）
        print(f"[review] ❌ {exc}", file=sys.stderr)
        return 2
    print(f"[review] ✅ {args.reviewer} 標註寫入 {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
