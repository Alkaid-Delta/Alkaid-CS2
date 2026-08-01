# -*- coding: utf-8 -*-
"""
review_schema.py — ReviewAnnotation schema 驗證（Phase 6.4C2-A.2/A.3）

必要欄位：expected_items / expected_raw_vision_safe / expected_safe_for_production。

- annotations 必須 dict、不得空
- expected_items 必須 list，每個元素必須 dict（受控 item schema）
- item name 非空 str；image_indexes 必須 list[int]（非 bool、>=0）
- safe flags 必須真正 bool
- 未知欄位拒絕（或只能放 extensions 白名單）
- 整份 annotations 遞迴 privacy scan（scan_redaction_issues），
  任何 error finding 拒絕（含 nested token/cookie/sender/email/URL/bytes...）
- 不得用 str(annotations) 作主要 privacy 驗證
"""
from __future__ import annotations

import re
from typing import Any

REQUIRED_ANNOTATION_FIELDS = (
    "expected_items",
    "expected_raw_vision_safe",
    "expected_safe_for_production",
)

# 允許的額外標註欄位（extension 白名單）
ALLOWED_ANNOTATION_EXTENSIONS = frozenset({
    "seller_price", "currency", "wear", "stattrak", "item_image_indexes",
    "image_kind", "should_create_price", "role", "notes",
    "conflict_reason", "reviewer_comments",
})

REVIEW_SCHEMA_VERSION = "real-review-v1"

# 受控 item schema 允許欄位
ALLOWED_ITEM_FIELDS = frozenset({
    "name", "wear", "stattrak", "image_indexes", "price", "currency",
    "role", "notes", "market_hash_name",
})

# 這些 hash 欄位由專用 SHA-256 validator 驗證，不得被 generic base64 誤判
SHA256_FIELDS = frozenset({
    "fixture_sha256", "original_image_hashes", "redacted_image_hashes",
    "reviewer_inputs_hash", "final_ground_truth_hash",
})
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def validate_sha256(value: Any) -> bool:
    """64 位小寫 hex SHA-256。"""
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def validate_annotations(annotations: Any) -> list[str]:
    """驗證 review annotations，回傳錯誤清單（空=通過）。

    必須對整份 annotations 執行遞迴 privacy scan；
    不得用 str(annotations) 作主要驗證。
    """
    from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
        scan_redaction_issues,
    )
    errors: list[str] = []
    if not isinstance(annotations, dict):
        return ["annotations_not_dict"]
    if not annotations:
        return ["annotations_empty"]
    for f in REQUIRED_ANNOTATION_FIELDS:
        if f not in annotations:
            errors.append(f"missing_required:{f}")
    if "expected_items" in annotations and \
            not isinstance(annotations["expected_items"], list):
        errors.append("expected_items_not_list")
    if isinstance(annotations.get("expected_items"), list):
        for i, item in enumerate(annotations["expected_items"]):
            if not isinstance(item, dict):
                errors.append(f"expected_items[{i}]_not_dict")
                continue
            _validate_item(item, i, errors)
    for f in ("expected_raw_vision_safe", "expected_safe_for_production"):
        if f in annotations and not isinstance(annotations[f], bool):
            errors.append(f"{f}_not_bool")
    unknown = set(annotations) - set(REQUIRED_ANNOTATION_FIELDS) \
        - ALLOWED_ANNOTATION_EXTENSIONS
    if unknown:
        errors.append(f"unknown_fields:{','.join(sorted(unknown))}")
    # Phase 6.4C2-A.4：頂層 annotation 型別收尾
    if "stattrak" in annotations and \
            not isinstance(annotations["stattrak"], bool):
        errors.append("stattrak_not_bool")
    if "should_create_price" in annotations and \
            not isinstance(annotations["should_create_price"], bool):
        errors.append("should_create_price_not_bool")
    if "item_image_indexes" in annotations:
        idxs = annotations["item_image_indexes"]
        if not isinstance(idxs, list):
            errors.append("item_image_indexes_not_list")
        else:
            for v in idxs:
                if isinstance(v, bool) or not isinstance(v, int):
                    errors.append(f"item_image_indexes_not_int:{v!r}")
                elif v < 0:
                    errors.append(f"item_image_indexes_negative:{v}")
    if "role" in annotations and annotations["role"] not in (
            "selling", "buying", "trade"):
        errors.append(f"role_invalid:{annotations['role']!r}")
    if "currency" in annotations and annotations["currency"] is not None \
            and annotations["currency"] not in ("TWD", "RMB", "USD"):
        errors.append(f"currency_invalid:{annotations['currency']!r}")
    # Phase 6.4C2-A.5：頂層 nullable（不確定 → None，不得逼 reviewer 猜測）
    if "seller_price" in annotations and \
            not _is_nullable_scalar(annotations["seller_price"]):
        errors.append(f"seller_price_invalid_type:"
                      f"{type(annotations['seller_price']).__name__}")
    if "wear" in annotations:
        wear = annotations["wear"]
        if wear is None:
            pass
        elif not isinstance(wear, str) or not wear.strip():
            errors.append(f"wear_invalid:{wear!r}")
    if "image_kind" in annotations and \
            annotations["image_kind"] not in (
                "single", "inventory_grid", "inventory", "market",
                "sell_orders", "detail", "sticker", "chat", "unknown"):
        errors.append(f"image_kind_invalid:{annotations['image_kind']!r}")
    # 對整份 annotations 遞迴 privacy scan（含 nested）
    findings = scan_redaction_issues(annotations)
    for f in findings:
        if f.severity == "error":
            errors.append(f"privacy:{f.code}:{f.field}")
    return errors


def _is_nullable_scalar(value: Any) -> bool:
    """允許 str / int / float / None；明確拒絕 bool / list / dict（bool 是 int 子類先拒）。"""
    if value is None:
        return True
    if isinstance(value, bool):
        return False
    if isinstance(value, (str, int, float)):
        return True
    return False


def _validate_item(item: dict, idx: int, errors: list[str]) -> None:
    """受控 item schema（Phase 6.4C2-A.5 nullable）：

    - name 非空 str
    - price：str/int/float/None（拒 bool/list/dict）
    - currency：None 或 TWD/RMB/USD（拒數字/bool/list/dict/其他字串）
    - wear：非空 str 或 None（拒空白/數字/bool/list/dict）
    - stattrak：bool 或 None
    - image_indexes list[int] 非 bool >=0；未知欄位拒絕
    """
    unknown = set(item) - ALLOWED_ITEM_FIELDS
    if unknown:
        errors.append(f"expected_items[{idx}].unknown_fields:"
                      f"{','.join(sorted(unknown))}")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        errors.append(f"expected_items[{idx}].name_missing_or_empty")
    if "wear" in item:
        wear = item["wear"]
        if wear is None:
            pass  # 不確定 → None 合法
        elif not isinstance(wear, str) or not wear.strip():
            errors.append(f"expected_items[{idx}].wear_invalid:{wear!r}")
    if "stattrak" in item and item["stattrak"] is not None and \
            not isinstance(item["stattrak"], bool):
        errors.append(f"expected_items[{idx}].stattrak_not_bool")
    if "image_indexes" in item:
        idxs = item["image_indexes"]
        if not isinstance(idxs, list):
            errors.append(f"expected_items[{idx}].image_indexes_not_list")
        else:
            for v in idxs:
                if isinstance(v, bool) or not isinstance(v, int):
                    errors.append(
                        f"expected_items[{idx}].image_indexes_not_int:{v!r}")
                elif v < 0:
                    errors.append(
                        f"expected_items[{idx}].image_indexes_negative:{v}")
    if "price" in item and not _is_nullable_scalar(item["price"]):
        errors.append(f"expected_items[{idx}].price_invalid_type:"
                      f"{type(item['price']).__name__}")
    if "currency" in item:
        cur = item["currency"]
        if cur is not None and cur not in ("TWD", "RMB", "USD"):
            errors.append(f"expected_items[{idx}].currency_invalid:{cur!r}")
    if "role" in item and item["role"] not in ("selling", "buying", "trade"):
        errors.append(f"expected_items[{idx}].role_invalid:{item['role']!r}")


def validate_review_file(review_file: dict) -> list[str]:
    """驗證整份 review 檔。

    頂層只允許：schema_version / case_id / reviewer_id / reviewed_at / annotations。
    - schema_version == real-review-v1
    - reviewer_id 必須通過 REVIEWER_ID_ALLOWLIST
    - case_id 非空
    - reviewed_at 必填且為 UTC 格式 YYYY-MM-DDTHH:MM:SSZ
    - annotations 通過完整 schema
    - 對整份 review file 做遞迴 privacy scan
    """
    from alkaid_cs2.evaluation.intake_models import (  # noqa: E402
        REVIEWER_ID_ALLOWLIST,
    )
    from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
        scan_redaction_issues,
    )
    errors: list[str] = []
    if not isinstance(review_file, dict):
        return ["review_file_not_dict"]
    allowed_top = {"schema_version", "case_id", "reviewer_id",
                   "reviewed_at", "annotations"}
    unknown = set(review_file) - allowed_top
    if unknown:
        errors.append(f"unknown_top_level_fields:{','.join(sorted(unknown))}")
    if review_file.get("schema_version") != REVIEW_SCHEMA_VERSION:
        errors.append(f"schema_version_mismatch:{review_file.get('schema_version')!r}")
    reviewer_id = review_file.get("reviewer_id")
    if reviewer_id not in REVIEWER_ID_ALLOWLIST:
        errors.append(f"reviewer_id_invalid:{reviewer_id!r}")
    case_id = review_file.get("case_id")
    if not isinstance(case_id, str) or not case_id.strip():
        errors.append("case_id_missing")
    reviewed_at = review_file.get("reviewed_at")
    if not isinstance(reviewed_at, str) or not reviewed_at.strip():
        errors.append("reviewed_at_missing")
    elif not _UTC_TIMESTAMP_RE.match(reviewed_at):
        errors.append(f"reviewed_at_invalid_format:{reviewed_at!r}")
    else:
        # Phase 6.4C2-A.4：不只 regex——驗證真實 UTC 日期
        from datetime import datetime
        try:
            datetime.strptime(reviewed_at, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            errors.append(f"reviewed_at_invalid_date:{reviewed_at!r}")
    errors.extend(validate_annotations(review_file.get("annotations")))
    # 對整份 review file 做遞迴 privacy scan
    findings = scan_redaction_issues(review_file)
    for f in findings:
        if f.severity == "error":
            errors.append(f"privacy:{f.code}:{f.field}")
    return errors


_UTC_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
