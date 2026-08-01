# -*- coding: utf-8 -*-
"""
run_real_analyzer_evaluation.py — Real analyzer dry-run / execution CLI（Phase 6.4C2-B0/B0.1）

- 預設關閉：必須 --allow-external-analyzer + EVALUATION_ALLOW_EXTERNAL_ANALYZER=1
- eligible cases 從 validated manifest 產生（anonymized_real + double_review + privacy passed）
- 本階段只支援 dry-run（不載入 bytes、不建 adapter、不呼叫 network）
- audit/cache 只能寫入 local_data 子路徑
- 固定錯誤碼；不回顯 storage reference / case ID / path / bytes
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from alkaid_cs2.evaluation.external_analyzer_runner import (  # noqa: E402
    load_eligible_cases_from_manifest,
    resolve_local_data_subdir,
    run_dry_plan,
)

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_DATA = os.path.join(PROJECT_ROOT, "local_data")
CACHE_DIR = os.path.join(LOCAL_DATA, "evaluation_analyzer_cache")
AUDIT_DIR = os.path.join(LOCAL_DATA, "evaluation_analyzer_runs")
DEFAULT_MANIFEST = os.path.join(PROJECT_ROOT, "tests", "fixtures",
                                "evaluation_real", "manifest.json")

ENV_FLAG = "EVALUATION_ALLOW_EXTERNAL_ANALYZER"


def main(
    argv: list[str] | None = None,
    *,
    local_data_root_override: str | None = None,
) -> int:
    """Real analyzer dry-run CLI。

    - production local_data root 固定 PROJECT_ROOT/local_data（不可由 CLI/env/
      manifest 改寫）
    - test 隔離 root 只能透過 main(..., local_data_root_override=...) 程式內注入
    """
    ap = argparse.ArgumentParser(
        description="Real analyzer evaluation（本階段僅 dry-run）")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="dry-run 模式（本階段唯一支援模式）")
    ap.add_argument("--allow-external-analyzer", action="store_true",
                    help="授權 flag（仍需 env 才生效）")
    ap.add_argument("--manifest", default=DEFAULT_MANIFEST)
    ap.add_argument("--fixtures-dir", default=os.path.join(
        PROJECT_ROOT, "tests", "fixtures", "evaluation_real"))
    ap.add_argument("--cache-dir", default=CACHE_DIR)
    ap.add_argument("--audit-dir", default=AUDIT_DIR)
    ap.add_argument("--run-salt", default=None,
                    help="opaque key salt（預設隨機，不寫 Git 不顯示）")
    args = ap.parse_args(argv)

    if not args.dry_run:
        # Phase 6.4C2-B0.1：本階段只有 dry-run（default=False，明確要求 flag）
        print("[real-analyzer] dry_run_only", file=sys.stderr)
        return 2

    import uuid
    run_salt = args.run_salt or uuid.uuid4().hex
    env_allowed = os.environ.get(ENV_FLAG) == "1"

    # 輸出路徑 confine：root 固定 PROJECT_ROOT/local_data（或 test-only 注入）
    try:
        local_data_root = str(Path(
            local_data_root_override or LOCAL_DATA).resolve())
        cache_dir = resolve_local_data_subdir(args.cache_dir,
                                              local_data_root)
        audit_dir = resolve_local_data_subdir(args.audit_dir,
                                              local_data_root)
    except ValueError:
        print("[real-analyzer] output_path_not_allowed")
        return 2

    eligible_cases, load_errors = load_eligible_cases_from_manifest(
        args.manifest, args.fixtures_dir)
    if load_errors:
        for e in load_errors:
            print(f"[real-analyzer] {e}")
        return 2

    eligible_dicts = [c.as_dict() for c in eligible_cases]
    code, errors, plan = run_dry_plan(
        cli_allowed=args.allow_external_analyzer,
        env_allowed=env_allowed,
        eligible_cases=eligible_dicts,
        run_salt=run_salt,
        adapter_name="fake-analyzer",
        cache_dir=cache_dir,
        audit_dir=audit_dir,
    )
    if code != 0:
        for e in errors:
            print(f"[real-analyzer] {e}")
        return code
    print("[real-analyzer] dry_run_valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
