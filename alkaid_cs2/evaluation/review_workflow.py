# -*- coding: utf-8 -*-
"""
review_workflow.py — Reviewer A/B 獨立標註、review diff、adjudication（Phase 6.4C2-A/A.2）

- reviewer_a.json / reviewer_b.json 獨立輸出，不得互相覆寫
- reviewer identity 限 reviewer_a / reviewer_b / reviewer_c
- annotations 必須通過 ReviewAnnotation schema（write_review 與 load_reviews 都驗證）
- 所有關鍵欄位一致 → double_review；任一不一致 → disputed
- 不得自動選擇 A 或 B 作為正確答案
- adjudication 只限 disputed；強制 final Ground Truth（不得 None/空）；
  不得參考 parser/analyzer 輸出
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alkaid_cs2.evaluation.intake_models import (
    REVIEWER_ID_ALLOWLIST,
    validate_reviewer_id,
)
from alkaid_cs2.evaluation.intake_validation import (
    REVIEW_COMPARE_FIELDS,
    compare_review_annotations,
    compute_final_ground_truth_hash,
    compute_reviewer_inputs_hash,
)
from alkaid_cs2.evaluation.review_schema import (
    REVIEW_SCHEMA_VERSION,
    validate_annotations,
    validate_review_file,
)

REVIEW_FILENAMES = {"reviewer_a.json": "reviewer_a",
                    "reviewer_b.json": "reviewer_b"}


class AtomicWriteError(OSError):
    """adjudication 兩檔原子寫入失敗（Phase 6.4C2-A.6）。"""


def atomic_write_pair(first_path: Path, first_bytes: bytes,
                      second_path: Path, second_bytes: bytes) -> None:
    """將兩個檔案視為單一 atomic commit unit（Phase 6.4C2-A.6）。

    流程：
    1. 兩個 temp 檔（名稱含 nonce，避免碰撞）都寫入並 flush/fsync
    2. 兩個 temp 都成功後才進入 commit 階段
    3. 既有正式檔存在 → 先建立 backup
    4. 先 replace second（final GT）再 replace first（adjudication）——
       或依 rollback 安全性選擇順序
    5. 任一 replace 失敗 → 恢復既有檔（backup → 原路徑）、清除 temp
    6. 成功 → 清除 backup 與 temp

    失敗時拋 AtomicWriteError；不留下半套新檔；不刪除 reviewer A/B。
    """
    import os as _os
    import uuid as _uuid
    nonce = _uuid.uuid4().hex[:8]
    tmp1 = first_path.with_name(f"{first_path.name}.tmp.{nonce}")
    tmp2 = second_path.with_name(f"{second_path.name}.tmp.{nonce}")
    backup1 = first_path.with_name(f"{first_path.name}.bak.{nonce}")
    backup2 = second_path.with_name(f"{second_path.name}.bak.{nonce}")
    created_tmp: list[Path] = []
    created_bak: list[Path] = []
    committed: list[Path] = []
    first_existed = first_path.exists()
    second_existed = second_path.exists()
    # Phase 6.4C2-A.7：明確狀態
    commit_complete = False
    rollback_complete = False
    preserve_backups = False
    try:
        # ── 1. 兩個 temp 都寫入（fsync）──
        for tmp, data in ((tmp1, first_bytes), (tmp2, second_bytes)):
            with open(tmp, "wb") as fh:
                fh.write(data)
                fh.flush()
                _os.fsync(fh.fileno())
            created_tmp.append(tmp)
        # ── 2. 既有檔 backup ──
        if first_existed:
            _os.replace(first_path, backup1)
            created_bak.append(backup1)
        if second_existed:
            _os.replace(second_path, backup2)
            created_bak.append(backup2)
        # ── 3. commit：先 replace second 再 first（rollback 時可逐項處理）──
        _os.replace(tmp2, second_path)
        committed.append(second_path)
        created_tmp.remove(tmp2)
        _os.replace(tmp1, first_path)
        committed.append(first_path)
        created_tmp.remove(tmp1)
        commit_complete = True
    except OSError:
        # ── 4. rollback：不留下半套新狀態 ──
        try:
            # 4a. 已 commit 的新檔：有舊版 → 從 backup 恢復；無舊版 → 刪除
            for path in committed:
                if path == first_path and first_existed and backup1.exists():
                    _os.replace(backup1, first_path)
                elif path == second_path and second_existed and backup2.exists():
                    _os.replace(backup2, second_path)
                elif path.exists():
                    path.unlink()
            # 4b. 未 commit 但舊檔已被搬去 backup → 恢復
            if first_existed and backup1.exists() and not first_path.exists():
                _os.replace(backup1, first_path)
            if second_existed and backup2.exists() and not second_path.exists():
                _os.replace(backup2, second_path)
            rollback_complete = True
        except OSError:
            # Phase 6.4C2-A.7：rollback 自身失敗 → 保留 backup 供人工復原
            preserve_backups = True
        raise AtomicWriteError("adjudication atomic write failed") from None
    finally:
        for tmp in (tmp1, tmp2):
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
        # Phase 6.4C2-A.7：只有 commit 或 rollback 完整成功才清除 backup；
        # rollback 不完整時 backup 是唯一 recovery copy，不得自動刪除
        if commit_complete or rollback_complete:
            for bak in (backup1, backup2):
                if bak.exists():
                    try:
                        bak.unlink()
                    except OSError:
                        pass
        # preserve_backups=True 時 backup 保留（供人工復原）


def write_review(review_dir: Path, reviewer_id: str, annotations: dict,
                 case_id: str) -> Path:
    """寫單一 reviewer 標註檔（reviewer_a.json / reviewer_b.json）。

    - reviewer_id 必須 reviewer_a/b/c（不得真實姓名）
    - annotations 必須通過 ReviewAnnotation schema（空 dict/缺欄位/未知欄位拒絕）
    - 檔名由 reviewer_id 決定，結構上無法覆寫另一位 reviewer
    """
    if not validate_reviewer_id(reviewer_id):
        raise ValueError(f"reviewer identity 不合格：{reviewer_id!r}"
                         "（只允許 reviewer_a/reviewer_b/reviewer_c，不得真實姓名）")
    fname = f"{reviewer_id}.json"
    if fname not in REVIEW_FILENAMES and reviewer_id != "reviewer_c":
        raise ValueError(f"不支援的 reviewer 檔案：{fname}")
    schema_errors = validate_annotations(annotations)
    if schema_errors:
        raise ValueError(f"annotations schema 驗證失敗：{', '.join(schema_errors)}")
    if not isinstance(case_id, str) or not case_id.strip():
        raise ValueError("case_id 必填")

    payload = {
        "schema_version": REVIEW_SCHEMA_VERSION,
        "case_id": case_id,
        "reviewer_id": reviewer_id,
        "reviewed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "annotations": annotations,
    }
    review_dir.mkdir(parents=True, exist_ok=True)
    p = review_dir / fname
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                   encoding="utf-8")
    tmp.replace(p)
    return p


def load_reviews(review_dir: Path) -> tuple[dict | None, dict | None]:
    """讀 reviewer_a/b 並完整驗證（schema、reviewer_id、case_id、annotations）。

    - schema_version == real-review-v1
    - reviewer_a.json 內 reviewer_id == reviewer_a
    - reviewer_b.json 內 reviewer_id == reviewer_b
    - case_id 非空且 A/B 相同
    - annotations 通過 ReviewAnnotation schema
    - review 不得含 raw_text/URL/token/cookie/bytes

    任一驗證失敗 → ValueError（不靜默放行）。
    不存在 → None。
    """
    a = b = None
    pa = review_dir / "reviewer_a.json"
    pb = review_dir / "reviewer_b.json"
    if pa.exists():
        data = json.loads(pa.read_text(encoding="utf-8"))
        _validate_loaded_review(data, "reviewer_a")
        a = data
    if pb.exists():
        data = json.loads(pb.read_text(encoding="utf-8"))
        _validate_loaded_review(data, "reviewer_b")
        b = data
    if a is not None and b is not None and a.get("case_id") != b.get("case_id"):
        raise ValueError(
            f"reviewer A/B case_id 不一致：{a.get('case_id')!r} vs "
            f"{b.get('case_id')!r}")
    return a, b


def _validate_loaded_review(data: dict, expected_reviewer: str) -> None:
    errors = validate_review_file(data)
    if data.get("reviewer_id") != expected_reviewer:
        errors.append(f"reviewer_id_mismatch:{data.get('reviewer_id')!r}")
    if errors:
        raise ValueError(f"{expected_reviewer} review 驗證失敗："
                         f"{', '.join(errors)}")


def compute_review_decision(review_dir: Path) -> dict:
    """依 reviewer_a/b 標註計算決策（含 case_id）。"""
    a, b = load_reviews(review_dir)
    case_id = (a or b or {}).get("case_id", "")
    if a is None or b is None:
        return {"decision": "single_review",
                "case_id": case_id,
                "reason": "missing_reviewer_b" if a is not None
                else "missing_reviewer_a_or_b",
                "field_results": {}, "disputed_fields": []}
    ann_a = a["annotations"]
    ann_b = b["annotations"]
    # 兩份 review 同時缺必要欄位 → 不得 double_review
    comparison = compare_review_annotations(ann_a, ann_b)
    decision = "double_review" if comparison["decision"] == "double_review" \
        else "disputed"
    return {
        "decision": decision,
        "case_id": case_id,
        "field_results": comparison["field_results"],
        "disputed_fields": comparison["disputed_fields"],
        "reviewer_inputs_hash": compute_reviewer_inputs_hash(ann_a, ann_b),
    }


def adjudicate_disputed(
    review_dir: Path,
    *,
    adjudicator: str,
    decision_reason: str,
    case_id: str | None = None,
    final_ground_truth: dict | None = None,
    parser_predictions: Any = None,
    analyzer_output: Any = None,
    write_files: bool = True,
) -> dict:
    """Adjudication（6.4C2-A.2/A.3 強化）：

    - 只有 disputed 可進
    - adjudicator 限 reviewer_c（或明確 adjudicator 名；不得真實姓名）
    - **final_ground_truth 必填**：不得 None、不得空 dict、必須過 ReviewAnnotation schema
    - case_id 必須與 review case_id 相同
    - 不得參考 parser 預測 / analyzer 輸出
    - write_files=False（dry-run）：完整驗證但只跳過檔案寫入
    - 保存 adjudication.json + final_ground_truth.json（canonical）
    - 不刪除原 reviewer A/B 標註
    """
    from alkaid_cs2.evaluation.review_schema import validate_annotations

    current = compute_review_decision(review_dir)
    if current["decision"] != "disputed":
        raise ValueError(
            f"只有 disputed 可進 adjudication（目前 {current['decision']}）")
    if parser_predictions is not None or analyzer_output is not None:
        raise ValueError("adjudication 不得參考 parser 預測或 analyzer 輸出")
    if not validate_reviewer_id(adjudicator) and adjudicator != "adjudicator":
        raise ValueError(f"adjudicator 不合格：{adjudicator!r}")
    if not decision_reason or not decision_reason.strip():
        raise ValueError("decision reason 必填")
    if final_ground_truth is None:
        raise ValueError("final_ground_truth 必填（不得 None）")
    if not isinstance(final_ground_truth, dict) or not final_ground_truth:
        raise ValueError("final_ground_truth 不得為空 dict")
    gt_errors = validate_annotations(final_ground_truth)
    if gt_errors:
        raise ValueError(f"final_ground_truth schema 驗證失敗："
                         f"{', '.join(gt_errors)}")
    if case_id is not None and case_id != current["case_id"]:
        raise ValueError(
            f"case_id 不一致：CLI={case_id!r} review={current['case_id']!r}")

    a, b = load_reviews(review_dir)
    record = {
        "schema_version": "real-adjudication-v1",
        "case_id": current["case_id"],
        "adjudicated_by": adjudicator,
        "adjudication_reason": decision_reason,
        "adjudication_timestamp": datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        "reviewer_inputs_hash": compute_reviewer_inputs_hash(
            (a or {}).get("annotations", {}), (b or {}).get("annotations", {})),
        "final_ground_truth_hash": compute_final_ground_truth_hash(
            final_ground_truth),
        "final_review_status": "double_review",
    }
    if not write_files:
        return record  # dry-run：完整驗證，不寫檔（不建 temp/backup）
    review_dir.mkdir(parents=True, exist_ok=True)
    # Phase 6.4C2-A.6：adjudication.json + final_ground_truth.json
    # 是同一 atomic commit unit（任一失敗 → rollback，不留下半套）
    adjudication_bytes = json.dumps(record, ensure_ascii=False,
                                    indent=2).encode("utf-8")
    final_gt_bytes = json.dumps(
        final_ground_truth, sort_keys=True, ensure_ascii=False,
        separators=(",", ":")).encode("utf-8")
    atomic_write_pair(
        review_dir / "adjudication.json", adjudication_bytes,
        review_dir / "final_ground_truth.json", final_gt_bytes)
    return record
