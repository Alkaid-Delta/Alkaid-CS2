#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
redact_real_case.py — 真實案例匿名化 CLI（Phase 6.4C2-A）

- 接收本機 JSON → 產生可進 fixtures 的匿名化 draft
- deterministic、不呼叫 LLM、不連網、不自動補 Ground Truth
- privacy gate 全過 → source=anonymized_real；否則 unverified_real_draft
- 預設輸出 local_data/real_intake/redacted/（Git 不追蹤）

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

from alkaid_cs2.evaluation.redaction import redact_real_case_input

DEFAULT_OUTPUT = Path("local_data/real_intake/redacted")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Redact Real Case")
    ap.add_argument("--input", required=True, help="本機原始 JSON（含 raw_text 等）")
    ap.add_argument("--output", default=None)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--source-provenance", required=True,
                    choices=sorted({"user_supplied_real",
                                    "user_authorized_collection",
                                    "internal_owned_source"}))
    ap.add_argument("--authorization", required=True,
                    choices=sorted({"user_supplied", "owner_authorized",
                                    "internal_owned"}))
    ap.add_argument("--redaction-version", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    p = Path(args.input)
    if not p.exists():
        print(f"[redact] ❌ input 不存在：{p}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        print(f"[redact] ❌ input 無法解析：{exc}", file=sys.stderr)
        return 2
    if not isinstance(raw, dict):
        print("[redact] ❌ input 必須是 JSON object", file=sys.stderr)
        return 2

    try:
        draft, reasons = redact_real_case_input(
            raw, case_id=args.case_id, redaction_version=args.redaction_version,
            provenance=args.source_provenance, authorization=args.authorization)
    except ValueError as exc:
        print(f"[redact] ❌ {exc}", file=sys.stderr)
        return 2

    # dry-run / 常規都只顯示驗證摘要（不得打印 raw_text 全文）
    source = draft["source"]
    n_err = sum(1 for r in reasons if r.startswith("privacy:"))
    print(f"[redact] case_id={draft['case_id']} source={source} "
          f"reasons={len(reasons)}（privacy findings {n_err}）")
    if source != "anonymized_real":
        for r in reasons:
            print(f"[redact]   ⚠️ {r}")
    if args.dry_run:
        print("[redact] dry-run：不寫檔")
        return 0

    out_dir = Path(args.output) if args.output else DEFAULT_OUTPUT
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{draft['case_id']}.draft.json"
    out.write_text(json.dumps(draft, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"[redact] ✅ draft 寫入 {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
