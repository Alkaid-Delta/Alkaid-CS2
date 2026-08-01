#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
create_real_case_intake.py — 建立真實案例 Intake Manifest（Phase 6.4C2-A）

- 從使用者提供的已匿名化 JSON 建立 intake manifest
- 不接收原始圖片 bytes、不連網、不自動推測 provenance
- 缺少必填欄位 / provenance 或 authorization 不合格 → exit 2
- 預設輸出 local_data/real_intake/（Git 不追蹤）
- --dry-run：不寫檔、只顯示 validation 結果、不輸出敏感內容

exit code：0 成功 / 1 workflow 錯誤 / 2 schema/validation 錯誤
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.intake_models import (
    RealCaseIntakeManifest,
    validate_authorization,
    validate_provenance,
    validate_secure_store_reference,
)

DEFAULT_OUTPUT = Path("local_data/real_intake")


def build_manifest(args) -> tuple[RealCaseIntakeManifest | None, int]:
    """建 manifest；回傳 (manifest, exit_code)。"""
    errors: list[str] = []
    if not validate_provenance(args.source_provenance):
        # Phase 6.4C2-A.7：固定碼（不回顯原始 provenance）
        errors.append("invalid_provenance")
    if not validate_authorization(args.authorization):
        errors.append("invalid_authorization")
    if not args.case_id:
        errors.append("case_id_missing")
    if not args.redaction_version:
        errors.append("redaction_version_missing")

    input_payload = None
    if args.input:
        p = Path(args.input)
        if not p.exists():
            errors.append("input_not_found")
        else:
            try:
                input_payload = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                # Phase 6.4C2-A.7：不回顯 JSON fragment
                errors.append("input_invalid_json")
            except OSError:
                errors.append("input_read_failed")
            else:
                # Phase 6.4C2-A.2：全面遞迴掃描（nested token/cookie/bytes/URL...）
                from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
                    scan_redaction_issues,
                )
                findings = scan_redaction_issues(input_payload)
                errs = [f for f in findings if f.severity == "error"]
                if errs:
                    codes = sorted({f.code for f in errs})
                    errors.append(f"input_sensitive:{','.join(codes)}")
                # 檢查不接收原始圖片 bytes
                if isinstance(input_payload, dict) and (
                        "image_bytes" in input_payload or
                        "raw_bytes" in input_payload or
                        "base64" in input_payload):
                    errors.append("input_contains_image_bytes")
                if isinstance(input_payload, dict) and \
                        "original_storage_reference" in input_payload and \
                        not validate_secure_store_reference(
                            str(input_payload["original_storage_reference"])):
                    errors.append("storage_reference_invalid")
                # Phase 6.4C2-A.2/A.4：image hash schema（受控 failure，不 traceback）
                if isinstance(input_payload, dict):
                    from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
                        validate_image_hashes,
                    )
                    raw_count = input_payload.get("image_count", 0)
                    # Phase 6.4C2-A.4：嚴格型別——不接受 "1"/1.0/true/false/負數
                    # （不得用 int(raw_count) 自動轉型）
                    if isinstance(raw_count, bool) or \
                            not isinstance(raw_count, int):
                        errors.append("image_count_invalid_type")
                        image_count = -1
                    elif raw_count < 0:
                        errors.append("image_count_negative")
                        image_count = -1
                    else:
                        image_count = raw_count
                    hash_errors = validate_image_hashes(
                        original_image_hashes=input_payload.get(
                            "original_image_hashes", []),
                        redacted_image_hashes=input_payload.get(
                            "redacted_image_hashes", []),
                        image_count=image_count)
                    if hash_errors:
                        errors.append("image_hash_validation_failed")

    if errors:
        for e in errors:
            print(f"[intake] ❌ {e}")
        return None, 2

    storage_ref = args.storage_reference
    if storage_ref is None and isinstance(input_payload, dict):
        storage_ref = input_payload.get("original_storage_reference")
    if not storage_ref or not validate_secure_store_reference(storage_ref):
        # Phase 6.4C2-A.8.1：固定碼（不回顯原始值）
        print("[intake] ❌ storage_reference_invalid")
        return None, 2

    # Phase 6.4C2-A.5：CLI metadata（含 notes）受 privacy gate 保護
    from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
        scan_redaction_issues,
    )
    for meta_key in ("notes", "case_id", "redaction_version", "redacted_by",
                     "collection_method", "collected_at"):
        meta_val = getattr(args, meta_key, None)
        if meta_val is None:
            continue
        findings = scan_redaction_issues({meta_key: str(meta_val)})
        errs = [f for f in findings if f.severity == "error"]
        if errs:
            # 只輸出 finding code 與欄位路徑（不回顯完整值）
            codes = sorted({f.code for f in errs})
            print(f"[intake] ❌ cli_metadata.{meta_key}:{','.join(codes)}")
            return None, 2

    manifest = None
    try:
        manifest = RealCaseIntakeManifest(
            intake_id=f"intake-{uuid.uuid4().hex[:12]}",
            case_id=args.case_id,
            source_type=(input_payload or {}).get("source_type", "post")
            if isinstance(input_payload, dict) else "post",
            source_provenance=args.source_provenance,
            consent_or_authorization=args.authorization,
            original_storage_reference=storage_ref,
            redaction_version=args.redaction_version,
            collected_at=(input_payload or {}).get("collected_at")
            if isinstance(input_payload, dict) else None,
            collection_method=(input_payload or {}).get("collection_method")
            if isinstance(input_payload, dict) else None,
            redaction_status="complete",
            redacted_by=args.redacted_by or "user",
            privacy_scan_status="pending",
            image_count=(input_payload or {}).get("image_count", 0)
            if isinstance(input_payload, dict) else 0,
            original_image_hashes=(input_payload or {}).get(
                "original_image_hashes", [])
            if isinstance(input_payload, dict) else [],
            redacted_image_hashes=(input_payload or {}).get(
                "redacted_image_hashes", [])
            if isinstance(input_payload, dict) else [],
            notes=args.notes,
        )
    except ValueError:
        # Phase 6.4C2-A.6：只輸出固定 error code（不回顯 str(exc)/repr/原始值）
        print("[intake] ❌ manifest_validation_failed", file=sys.stderr)
        return None, 2
    except (TypeError, OSError):
        print("[intake] ❌ manifest_validation_error", file=sys.stderr)
        return None, 2
    return manifest, 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Real Case Intake Manifest")
    ap.add_argument("--input", default=None,
                    help="已匿名化 JSON（可選；不含原始 bytes）")
    ap.add_argument("--output", default=None)
    ap.add_argument("--case-id", required=True)
    ap.add_argument("--source-provenance", required=True)
    ap.add_argument("--authorization", required=True)
    ap.add_argument("--redaction-version", required=True)
    ap.add_argument("--storage-reference", default=None,
                    help="secure-store://<opaque-id>")
    ap.add_argument("--redacted-by", default=None)
    ap.add_argument("--notes", default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    manifest, code = build_manifest(args)
    if manifest is None:
        return code

    if args.dry_run:
        # Phase 6.4C2-A.8：固定成功碼（不回顯 case_id/provenance/authorization/
        # storage reference/input/output path/hashes/notes）
        print("[intake] dry_run_valid")
        return 0

    out_dir = Path(args.output) if args.output else DEFAULT_OUTPUT
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out = out_dir / f"{manifest.case_id}.intake.json"
        out.write_text(json.dumps(manifest.to_dict(), ensure_ascii=False,
                                  indent=2), encoding="utf-8")
    except OSError:
        # Phase 6.4C2-A.6：固定錯誤碼（不輸出可能含敏感路徑的完整錯誤字串）
        print("[intake] ❌ manifest_write_failed", file=sys.stderr)
        return 2
    # Phase 6.4C2-A.8：固定成功碼（不回顯 output path/case_id/filename）
    print("[intake] manifest_written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
