# -*- coding: utf-8 -*-
"""
analyzer_cache.py — Git 外 analyzer cache（Phase 6.4C2-B0 / B0.1）

- 結果只能寫入 Git 外：local_data/evaluation_analyzer_cache/
- cache record 只允許受控欄位（不含 bytes/base64/storage reference/case ID/
  raw response/headers/token/endpoint）
- normalized_result 必須通過嚴格 allowlist schema（未知欄位拒絕）
- 原子寫入：canonical bytes → 同目錄唯一 temp → write+flush+fsync → os.replace
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from pathlib import Path

from alkaid_cs2.evaluation.intake_validation import (
    scan_redaction_issues,
)

CACHE_SCHEMA_VERSION = "analyzer-cache-v1"

ALLOWED_CACHE_FIELDS = frozenset({
    "schema_version", "opaque_case_key", "image_index", "image_sha256",
    "analyzer_name", "analyzer_version", "analyzed_at", "normalized_result",
    "result_sha256", "status",
})

ALLOWED_RESULT_FIELDS = frozenset({"kind", "item_count", "items", "warnings"})
ALLOWED_ITEM_FIELDS = frozenset({
    "name", "wear", "currency", "price", "stattrak", "image_index",
})
ALLOWED_KINDS = frozenset({"image", "image_group", "unknown"})
ALLOWED_WEARS = frozenset({
    "Factory New", "Minimal Wear", "Field-Tested", "Well-Worn",
    "Battle-Scarred", None,
})
ALLOWED_CURRENCIES = frozenset({"TWD", "RMB", "USD", None})
ALLOWED_STATUS = frozenset({"success", "failed", "skipped"})

# opaque case key / image hash：sha256 小寫 hex
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
# 真實 UTC timestamp：YYYY-MM-DDTHH:MM:SSZ
_UTC_TS_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class AnalyzerCacheWriteError(Exception):
    """analyzer cache 寫入失敗（固定錯誤碼）。"""


def compute_opaque_case_key(case_id: str, run_salt: str) -> str:
    """由 case_id + run_salt 產生不可逆 opaque key。"""
    return hashlib.sha256(
        f"{case_id}::{run_salt}".encode("utf-8")).hexdigest()


def _is_utc_timestamp(value: str) -> bool:
    import datetime
    if not _UTC_TS_RE.match(value):
        return False
    try:
        datetime.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ")
        return True
    except ValueError:
        return False


def validate_normalized_result(result: dict) -> list[str]:
    """嚴格 normalized result schema；回傳錯誤清單（空 = 合法）。"""
    errors: list[str] = []
    if not isinstance(result, dict):
        return ["result_not_dict"]
    unknown = set(result) - ALLOWED_RESULT_FIELDS
    if unknown:
        errors.append(f"unknown_fields:{','.join(sorted(unknown))}")
    if "kind" not in result:
        errors.append("kind_missing")
    elif not isinstance(result["kind"], str) or \
            result["kind"] not in ALLOWED_KINDS:
        errors.append("kind_invalid")
    if "item_count" not in result:
        errors.append("item_count_missing")
    elif isinstance(result["item_count"], bool) or \
            not isinstance(result["item_count"], int) or \
            result["item_count"] < 0:
        errors.append("item_count_invalid")
    if "items" not in result:
        errors.append("items_missing")
    elif not isinstance(result["items"], list):
        errors.append("items_invalid")
    else:
        if result.get("item_count") != len(result["items"]):
            errors.append("item_count_mismatch")
        for i, item in enumerate(result["items"]):
            _validate_item(item, i, errors)
    if "warnings" in result:
        warnings_list = result["warnings"]
        if not isinstance(warnings_list, list):
            errors.append("warnings_invalid")
        else:
            if len(warnings_list) > 50:
                errors.append("warnings_too_many")
            for i, w in enumerate(warnings_list):
                if not isinstance(w, str) or not w.strip():
                    errors.append(f"warnings_{i}_invalid")
                elif len(w) > 200:
                    errors.append(f"warnings_{i}_too_long")
    # 整份 result 遞迴 privacy scan
    findings = scan_redaction_issues(result)
    for f in findings:
        if f.severity == "error":
            errors.append(f"privacy:{f.code}:{f.field}")
    return errors


def _validate_item(item: dict, idx: int, errors: list[str]) -> None:
    if not isinstance(item, dict):
        errors.append(f"item_{idx}_not_dict")
        return
    unknown = set(item) - ALLOWED_ITEM_FIELDS
    if unknown:
        errors.append(f"item_{idx}_unknown_fields:{','.join(sorted(unknown))}")
    if not isinstance(item.get("name"), str) or not item["name"].strip():
        errors.append(f"item_{idx}_name_invalid")
    if "wear" in item and item["wear"] is not None and \
            item["wear"] not in ALLOWED_WEARS:
        errors.append(f"item_{idx}_wear_invalid")
    if "currency" in item and item["currency"] is not None and \
            item["currency"] not in ALLOWED_CURRENCIES:
        errors.append(f"item_{idx}_currency_invalid")
    if "price" in item and item["price"] is not None:
        p = item["price"]
        if isinstance(p, bool) or not isinstance(p, (str, int, float)):
            errors.append(f"item_{idx}_price_invalid_type")
    if "stattrak" in item and item["stattrak"] is not None and \
            not isinstance(item["stattrak"], bool):
        errors.append(f"item_{idx}_stattrak_invalid")
    if "image_index" in item and item["image_index"] is not None:
        ii = item["image_index"]
        if isinstance(ii, bool) or not isinstance(ii, int) or ii < 0:
            errors.append(f"item_{idx}_image_index_invalid")


def build_cache_record(
    *,
    opaque_case_key: str,
    image_index: int,
    image_sha256: str,
    analyzer_name: str,
    analyzer_version: str,
    analyzed_at: str,
    normalized_result: dict,
    status: str = "success",
) -> dict:
    """建立 cache record；先驗證 normalized_result（不得建立明顯非法 record）。"""
    result_errors = validate_normalized_result(normalized_result)
    if result_errors:
        raise AnalyzerCacheWriteError("cache_normalized_result_invalid")
    result_bytes = json.dumps(normalized_result, sort_keys=True,
                              ensure_ascii=False,
                              separators=(",", ":")).encode("utf-8")
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "opaque_case_key": opaque_case_key,
        "image_index": image_index,
        "image_sha256": image_sha256,
        "analyzer_name": analyzer_name,
        "analyzer_version": analyzer_version,
        "analyzed_at": analyzed_at,
        "normalized_result": normalized_result,
        "result_sha256": hashlib.sha256(result_bytes).hexdigest(),
        "status": status,
    }


def _canonical_bytes(record: dict) -> bytes:
    return json.dumps(record, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def validate_cache_record(record: dict) -> list[str]:
    """完整 cache record 驗證（Phase 6.4C2-B0.1）；回傳錯誤清單。"""
    errors: list[str] = []
    unknown = set(record) - ALLOWED_CACHE_FIELDS
    if unknown:
        errors.append(f"unknown_fields:{','.join(sorted(unknown))}")
    if record.get("schema_version") != CACHE_SCHEMA_VERSION:
        errors.append("schema_version_invalid")
    if not _SHA256_RE.match(str(record.get("opaque_case_key", ""))):
        errors.append("opaque_case_key_invalid")
    if not _SHA256_RE.match(str(record.get("image_sha256", ""))):
        errors.append("image_sha256_invalid")
    if isinstance(record.get("image_index"), bool) or \
            not isinstance(record.get("image_index"), int) or \
            record["image_index"] < 0:
        errors.append("image_index_invalid")
    if not isinstance(record.get("analyzer_name"), str) or \
            not record["analyzer_name"].strip():
        errors.append("analyzer_name_empty")
    if not isinstance(record.get("analyzer_version"), str) or \
            not record["analyzer_version"].strip():
        errors.append("analyzer_version_empty")
    if not isinstance(record.get("analyzed_at"), str) or \
            not _is_utc_timestamp(record["analyzed_at"]):
        errors.append("analyzed_at_invalid")
    if record.get("status") not in ALLOWED_STATUS:
        errors.append("status_invalid")
    result_sha = record.get("result_sha256")
    if not isinstance(result_sha, str) or not _SHA256_RE.match(result_sha):
        errors.append("result_sha256_invalid")
    else:
        # 重新 canonicalize normalized_result 並比對 hash
        norm = record.get("normalized_result")
        if isinstance(norm, dict):
            recomputed = hashlib.sha256(_canonical_bytes(norm)).hexdigest()
            if recomputed != result_sha:
                errors.append("result_sha256_mismatch")
    errors.extend(validate_normalized_result(
        record.get("normalized_result", {})))
    # 整份 record 遞迴 privacy scan
    findings = scan_redaction_issues(record)
    for f in findings:
        if f.severity == "error":
            errors.append(f"privacy:{f.code}:{f.field}")
    return errors


def write_analyzer_cache_record(
    record: dict,
    cache_dir: str | os.PathLike[str],
) -> str:
    """原子寫入 cache record（Git 外）；回傳寫入路徑。

    - 完整驗證（validate_cache_record）
    - 失敗 → AnalyzerCacheWriteError（固定碼，不含路徑/值）
    """
    record_errors = validate_cache_record(record)
    if record_errors:
        raise AnalyzerCacheWriteError("cache_record_invalid")
    data = _canonical_bytes(record)
    key = str(record["opaque_case_key"])
    idx = int(record["image_index"])
    try:
        cache_path = Path(cache_dir)
        cache_path.mkdir(parents=True, exist_ok=True)
        target = cache_path / f"{key}.{idx}.json"
        # 原子寫入：唯一 temp → write+flush+fsync → os.replace
        tmp = cache_path / f".{target.name}.tmp.{uuid.uuid4().hex[:8]}"
        try:
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except OSError:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise
    except OSError:
        raise AnalyzerCacheWriteError("cache_write_failed") from None
    return str(target)
