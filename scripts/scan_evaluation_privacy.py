#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
scan_evaluation_privacy.py — 隱私掃描 CLI（Phase 6.4C1）

python scripts/scan_evaluation_privacy.py --fixtures tests/fixtures/evaluation_real

exit code：
0 = 無 privacy error
1 = 有 privacy error
2 = schema / path 錯誤
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from alkaid_cs2.evaluation.dataset_loader import load_evaluation_case
from alkaid_cs2.evaluation.privacy import scan_fixture_for_sensitive_data


def main() -> int:
    ap = argparse.ArgumentParser(description="隱私掃描（匿名化案例治理）")
    ap.add_argument("--fixtures", required=True, help="fixture 目錄")
    args = ap.parse_args()

    fixtures_dir = Path(args.fixtures)
    if not fixtures_dir.is_dir():
        print(f"錯誤: 目錄不存在 {fixtures_dir}", file=sys.stderr)
        return 2

    files = sorted(fixtures_dir.glob("*.json"))
    if not files:
        print(f"錯誤: 目錄無 .json 檔案 {fixtures_dir}", file=sys.stderr)
        return 2

    total_errors = 0
    total_warnings = 0
    for p in files:
        try:
            case = load_evaluation_case(p)
        except (ValueError, TypeError) as exc:
            print(f"[schema] {p.name}: {exc}")
            total_errors += 1
            continue
        findings = scan_fixture_for_sensitive_data(case)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        total_errors += len(errors)
        total_warnings += len(warnings)
        status = "❌" if errors else ("⚠️" if warnings else "✅")
        print(f"{status} {p.name}: errors={len(errors)} warnings={len(warnings)}")
        for f in findings:
            print(f"    [{f.severity}] {f.code} @ {f.field}: {f.message}")

    print(f"\n總結: {len(files)} 檔案, {total_errors} errors, {total_warnings} warnings")
    if total_errors:
        print("結論: 有 privacy error（禁止進 dataset）")
        return 1
    print("結論: 無 privacy error（可進 dataset）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
