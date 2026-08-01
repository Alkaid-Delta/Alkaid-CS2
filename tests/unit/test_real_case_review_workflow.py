"""test_real_case_review_workflow.py — Review A/B + Adjudication 測試（Phase 6.4C2-A）"""
import json
import os
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.review_workflow import (  # noqa: E402
    AtomicWriteError,
    adjudicate_disputed,
    compute_review_decision,
    write_review,
)
from alkaid_cs2.evaluation.intake_validation import (  # noqa: E402
    compute_final_ground_truth_hash,
)


def _annotations(**kw):
    base = dict(
        expected_items=[{"name": "AK-47 | Redline", "wear": "Field-Tested"}],
        seller_price="5000",
        currency="TWD",
        wear="Field-Tested",
        stattrak=False,
        item_image_indexes=[0],
        expected_raw_vision_safe=True,
        expected_safe_for_production=True,
        image_kind="single",
        should_create_price=True,
        role="selling",
    )
    base.update(kw)
    return base


def _setup_reviews(tmp_path, ann_a, ann_b=None):
    write_review(tmp_path, "reviewer_a", ann_a, "real_001")
    if ann_b is not None:
        write_review(tmp_path, "reviewer_b", ann_b, "real_001")
    return tmp_path


# ---------------------------------------------------------------
# 十二、Review Workflow 測試
# ---------------------------------------------------------------
def test_identical_reviews_become_double_review(tmp_path):
    ann = _annotations()
    _setup_reviews(tmp_path, ann, dict(ann))
    d = compute_review_decision(tmp_path)
    assert d["decision"] == "double_review"
    assert d["disputed_fields"] == []


def test_price_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="5500"))
    d = compute_review_decision(tmp_path)
    assert d["decision"] == "disputed"
    assert "seller_price" in d["disputed_fields"]


def test_currency_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path, _annotations(currency="TWD"),
                   _annotations(currency="RMB"))
    assert compute_review_decision(tmp_path)["decision"] == "disputed"


def test_wear_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path, _annotations(wear="Field-Tested"),
                   _annotations(wear="Minimal Wear"))
    assert compute_review_decision(tmp_path)["decision"] == "disputed"


def test_item_count_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path,
                   _annotations(expected_items=[{"name": "A"}]),
                   _annotations(expected_items=[{"name": "A"}, {"name": "B"}]))
    assert compute_review_decision(tmp_path)["decision"] == "disputed"


def test_item_image_index_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path, _annotations(item_image_indexes=[0]),
                   _annotations(item_image_indexes=[1]))
    assert compute_review_decision(tmp_path)["decision"] == "disputed"


def test_safe_flag_mismatch_becomes_disputed(tmp_path):
    _setup_reviews(tmp_path,
                   _annotations(expected_safe_for_production=True),
                   _annotations(expected_safe_for_production=False))
    assert compute_review_decision(tmp_path)["decision"] == "disputed"


def test_missing_reviewer_b_stays_single_review(tmp_path):
    _setup_reviews(tmp_path, _annotations())
    d = compute_review_decision(tmp_path)
    assert d["decision"] == "single_review"


def test_disputed_excluded_from_readiness(tmp_path):
    # disputed 不得進 readiness：final status 必須 disputing 才有意義
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    d = compute_review_decision(tmp_path)
    assert d["decision"] == "disputed"


def test_adjudication_preserves_original_reviews(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    before_a = (tmp_path / "reviewer_a.json").read_text(encoding="utf-8")
    before_b = (tmp_path / "reviewer_b.json").read_text(encoding="utf-8")
    record = adjudicate_disputed(
        tmp_path, adjudicator="reviewer_c", decision_reason="以圖為準 5000",
        final_ground_truth=_annotations(seller_price="5000"))
    assert record["final_review_status"] == "double_review"
    assert (tmp_path / "reviewer_a.json").read_text(encoding="utf-8") == before_a
    assert (tmp_path / "reviewer_b.json").read_text(encoding="utf-8") == before_b


def test_adjudication_cannot_use_parser_output(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="parser"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x",
                            parser_predictions={"price": "5000"})


def test_adjudication_cannot_use_analyzer_output(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="analyzer"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x",
                            analyzer_output={"items": []})


def test_adjudication_only_for_disputed(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    with pytest.raises(ValueError, match="只有 disputed"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x")


def test_reviewer_real_names_rejected(tmp_path):
    with pytest.raises(ValueError, match="reviewer identity"):
        write_review(tmp_path, "王小明", _annotations(), "real_001")


def test_final_hash_deterministic():
    gt = {"expected_items": [{"name": "A", "wear": "FT"}], "seller_price": "5000"}
    assert compute_final_ground_truth_hash(gt) == compute_final_ground_truth_hash(
        dict(gt))
    assert compute_final_ground_truth_hash(gt) != compute_final_ground_truth_hash(
        {**gt, "seller_price": "5500"})


# ================================================================
# Phase 6.4C2-A.2 — ReviewAnnotation schema / adjudication 強化
# ================================================================
def test_empty_reviews_not_double_review(tmp_path):
    # 空 annotations 被 write_review 拒絕 → 無法 double_review
    with pytest.raises(ValueError, match="annotations_empty"):
        write_review(tmp_path, "reviewer_a", {}, "real_001")


def test_both_missing_required_not_double_review(tmp_path):
    # 兩份 review 同時缺必要欄位：不得 double_review（write 即拒絕）
    ann = {"expected_items": []}  # 缺 safe flags
    with pytest.raises(ValueError, match="missing_required"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_missing_expected_items_rejected(tmp_path):
    ann = {"expected_raw_vision_safe": True,
           "expected_safe_for_production": True}
    with pytest.raises(ValueError, match="missing_required:expected_items"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_missing_safe_flags_rejected(tmp_path):
    ann = {"expected_items": [{"name": "A"}]}
    with pytest.raises(ValueError, match="missing_required"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_non_dict_annotations_rejected(tmp_path):
    with pytest.raises(ValueError, match="annotations_not_dict"):
        write_review(tmp_path, "reviewer_a", ["not", "dict"], "real_001")


def test_unknown_annotation_field_rejected(tmp_path):
    ann = _annotations(fake_field=123)
    with pytest.raises(ValueError, match="unknown_fields"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_different_case_ids_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    # 篡改 B 的 case_id
    data = json.loads((tmp_path / "reviewer_b.json").read_text(encoding="utf-8"))
    data["case_id"] = "real_002"
    (tmp_path / "reviewer_b.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="case_id 不一致"):
        compute_review_decision(tmp_path)


def test_tampered_reviewer_id_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewer_id"] = "reviewer_b"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer_id_mismatch"):
        compute_review_decision(tmp_path)


def test_wrong_review_schema_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["schema_version"] = "old-schema"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="schema_version_mismatch"):
        compute_review_decision(tmp_path)


def test_review_raw_text_rejected(tmp_path):
    # Phase 6.4C2-A.3：raw_text 不在 extension 白名單 → unknown_fields 拒絕
    ann = _annotations(raw_text="售 A 算5000")
    with pytest.raises(ValueError, match="unknown_fields"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_compute_decision_returns_case_id(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    d = compute_review_decision(tmp_path)
    assert d["case_id"] == "real_001"


def test_adjudication_without_final_gt_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="final_ground_truth 必填"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x", final_ground_truth=None)


def test_empty_final_gt_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="不得為空 dict"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x", final_ground_truth={})


def test_adjudication_case_id_mismatch_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="case_id 不一致"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x", case_id="real_999",
                            final_ground_truth=_annotations())


def test_adjudication_record_has_case_id(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    record = adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                                 decision_reason="以圖為準",
                                 final_ground_truth=_annotations())
    assert record["case_id"] == "real_001"
    assert record["final_review_status"] == "double_review"


def test_final_ground_truth_file_written(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                        decision_reason="以圖為準",
                        final_ground_truth=_annotations(seller_price="5000"))
    assert (tmp_path / "adjudication.json").exists()
    gt_p = tmp_path / "final_ground_truth.json"
    assert gt_p.exists(), "final_ground_truth.json 必須保存"
    gt = json.loads(gt_p.read_text(encoding="utf-8"))
    assert gt["seller_price"] == "5000"
    # canonical：與 hash 驗證一致
    from alkaid_cs2.evaluation.intake_validation import compute_final_ground_truth_hash
    assert compute_final_ground_truth_hash(gt) == \
        compute_final_ground_truth_hash(_annotations(seller_price="5000"))


# ================================================================
# Phase 6.4C2-A.3 — Review schema 遞迴安全 / 頂層 / dry-run
# ================================================================
def test_nested_token_in_item_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "token": "sk-abc"}])
    with pytest.raises(ValueError, match="privacy:auth_key"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_nested_sender_in_item_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "sender": "王小明"}])
    with pytest.raises(ValueError, match="privacy:private_key"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_nested_bytes_in_item_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "image_bytes": b"123"}])
    with pytest.raises(ValueError, match="privacy:binary_key"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_expected_items_non_dict_element_rejected(tmp_path):
    ann = _annotations(expected_items=["not-a-dict"])
    with pytest.raises(ValueError, match="expected_items\\[0\\]_not_dict"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_expected_item_empty_name_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "  "}])
    with pytest.raises(ValueError, match="name_missing_or_empty"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_invalid_image_indexes_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "image_indexes": [0, True]}])
    with pytest.raises(ValueError, match="image_indexes_not_int"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_safe_flag_integer_rejected(tmp_path):
    ann = _annotations(expected_safe_for_production=1)
    with pytest.raises(ValueError, match="expected_safe_for_production_not_bool"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_nested_http_url_rejected(tmp_path):
    ann = _annotations(expected_items=[
        {"name": "A", "notes": "https://www.facebook.com/x"}])
    with pytest.raises(ValueError, match="privacy:http_url"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_review_top_level_raw_text_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["raw_text"] = "售 A 算5000"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown_top_level_fields"):
        compute_review_decision(tmp_path)


def test_review_top_level_token_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["token"] = "sk-abc"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown_top_level_fields"):
        compute_review_decision(tmp_path)


def test_review_unknown_top_level_field_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["mystery_field"] = 123
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="unknown_top_level_fields"):
        compute_review_decision(tmp_path)


def test_reviewer_real_name_rejected_by_schema(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewer_id"] = "王小明"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewer_id_invalid"):
        compute_review_decision(tmp_path)


def test_reviewed_at_missing_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    del data["reviewed_at"]
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_at_missing"):
        compute_review_decision(tmp_path)


def test_reviewed_at_invalid_format_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewed_at"] = "2026-08-01 12:00:00"  # 非 UTC Z 格式
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_at_invalid_format"):
        compute_review_decision(tmp_path)


# ---- Adjudication dry-run parity ----
def test_dry_run_empty_final_gt_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="不得為空 dict"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x", final_ground_truth={},
                            write_files=False)


def test_dry_run_invalid_final_gt_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="schema 驗證失敗"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x",
                            final_ground_truth={"expected_items": "bad"},
                            write_files=False)


def test_dry_run_case_mismatch_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="case_id 不一致"):
        adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                            decision_reason="x", case_id="real_999",
                            final_ground_truth=_annotations(),
                            write_files=False)


def test_dry_run_invalid_adjudicator_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    with pytest.raises(ValueError, match="adjudicator 不合格"):
        adjudicate_disputed(tmp_path, adjudicator="王小明",
                            decision_reason="x",
                            final_ground_truth=_annotations(),
                            write_files=False)


def test_dry_run_valid_writes_nothing(tmp_path):
    _setup_reviews(tmp_path, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    record = adjudicate_disputed(tmp_path, adjudicator="reviewer_c",
                                 decision_reason="以圖為準",
                                 final_ground_truth=_annotations(),
                                 write_files=False)
    assert record["final_review_status"] == "double_review"
    assert record["case_id"] == "real_001"
    assert not (tmp_path / "adjudication.json").exists(), "dry-run 不寫 adjudication.json"
    assert not (tmp_path / "final_ground_truth.json").exists(), \
        "dry-run 不寫 final_ground_truth.json"


# ================================================================
# Phase 6.4C2-A.4 — Timestamp 真實日期 / 頂層型別
# ================================================================
def test_reviewed_at_invalid_calendar_date_rejected(tmp_path):
    # regex 通過但真實日期無效（2026-99-99）→ rejected
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewed_at"] = "2026-99-99T12:00:00Z"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_at_invalid_date"):
        compute_review_decision(tmp_path)


def test_reviewed_at_invalid_hour_rejected(tmp_path):
    _setup_reviews(tmp_path, _annotations(), dict(_annotations()))
    data = json.loads((tmp_path / "reviewer_a.json").read_text(encoding="utf-8"))
    data["reviewed_at"] = "2026-08-01T25:00:00Z"
    (tmp_path / "reviewer_a.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="reviewed_at_invalid_date"):
        compute_review_decision(tmp_path)


def test_annotation_stattrak_wrong_type_rejected(tmp_path):
    ann = _annotations(stattrak="false")  # 字串非 bool
    with pytest.raises(ValueError, match="stattrak_not_bool"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_annotation_item_image_indexes_wrong_type_rejected(tmp_path):
    ann = _annotations(item_image_indexes=["0"])  # 字串 list
    with pytest.raises(ValueError, match="item_image_indexes_not_int"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_annotation_should_create_price_wrong_type_rejected(tmp_path):
    ann = _annotations(should_create_price=1)
    with pytest.raises(ValueError, match="should_create_price_not_bool"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_annotation_role_invalid_rejected(tmp_path):
    ann = _annotations(role="stealing")
    with pytest.raises(ValueError, match="role_invalid"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_annotation_currency_invalid_rejected(tmp_path):
    ann = _annotations(currency="JPY")
    with pytest.raises(ValueError, match="currency_invalid"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_annotation_image_kind_invalid_rejected(tmp_path):
    ann = _annotations(image_kind="hologram")
    with pytest.raises(ValueError, match="image_kind_invalid"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_wrong_type_reviews_cannot_form_double_review(tmp_path):
    # 型別錯誤的 review 被 write 拒絕 → 不可能 double_review
    with pytest.raises(ValueError, match="currency_invalid"):
        write_review(tmp_path, "reviewer_b", _annotations(currency=123),
                     "real_001")
    # 即使手動篡改檔案：load_reviews 的 schema 驗證拒絕（不得 double_review）
    _setup_reviews(tmp_path, _annotations(currency="TWD"),
                   _annotations(currency="TWD"))
    data = json.loads((tmp_path / "reviewer_b.json").read_text(encoding="utf-8"))
    data["annotations"]["currency"] = 123
    (tmp_path / "reviewer_b.json").write_text(
        json.dumps(data, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError, match="currency_invalid"):
        compute_review_decision(tmp_path)


# ================================================================
# Phase 6.4C2-A.5 — Nullable Ground Truth schema
# ================================================================
def _nullable_ann(**kw):
    ann = _annotations()
    ann.pop("seller_price", None)
    ann.pop("currency", None)
    ann.pop("wear", None)
    ann.update(kw)
    return ann


def test_item_price_none_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "price": None}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")  # 不 raise


def test_item_price_string_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "price": "5000"}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_price_number_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "price": 5000}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_price_bool_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "price": True}])
    with pytest.raises(ValueError, match="price_invalid_type"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_currency_none_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "currency": None}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_currency_twd_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "currency": "TWD"}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_currency_invalid_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "currency": 123}])
    with pytest.raises(ValueError, match="currency_invalid"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_wear_none_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "wear": None}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_wear_empty_string_rejected(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "wear": "  "}])
    with pytest.raises(ValueError, match="wear_invalid"):
        write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_item_stattrak_none_accepted(tmp_path):
    ann = _annotations(expected_items=[{"name": "A", "stattrak": None}])
    write_review(tmp_path, "reviewer_a", ann, "real_001")


def test_top_level_seller_price_none_accepted(tmp_path):
    write_review(tmp_path, "reviewer_a",
                 _nullable_ann(seller_price=None), "real_001")


def test_top_level_seller_price_bool_rejected(tmp_path):
    with pytest.raises(ValueError, match="seller_price_invalid_type"):
        write_review(tmp_path, "reviewer_a",
                     _nullable_ann(seller_price=True), "real_001")


def test_top_level_seller_price_list_rejected(tmp_path):
    with pytest.raises(ValueError, match="seller_price_invalid_type"):
        write_review(tmp_path, "reviewer_a",
                     _nullable_ann(seller_price=[5000]), "real_001")


def test_top_level_wear_none_accepted(tmp_path):
    write_review(tmp_path, "reviewer_a", _nullable_ann(wear=None), "real_001")


def test_top_level_wear_invalid_type_rejected(tmp_path):
    with pytest.raises(ValueError, match="wear_invalid"):
        write_review(tmp_path, "reviewer_a", _nullable_ann(wear=5), "real_001")


def test_nullable_annotations_can_form_double_review(tmp_path):
    # 兩份 review 都用 None（不確定）→ 一致 → double_review
    ann = _nullable_ann(seller_price=None, currency=None, wear=None)
    _setup_reviews(tmp_path, ann, dict(ann))
    d = compute_review_decision(tmp_path)
    assert d["decision"] == "double_review"


def test_none_not_converted_to_currency_or_price(tmp_path):
    # None 不得被 normalization 轉成猜測值（保持 None）
    from alkaid_cs2.evaluation.intake_validation import _semantic_equal
    assert not _semantic_equal(None, "TWD")
    assert not _semantic_equal(None, 0)
    assert not _semantic_equal(None, "unknown")


# ================================================================
# Phase 6.4C2-A.6 — Atomic adjudication pair write
# ================================================================
def _flaky_replace(monkeypatch, fail_at):
    """模擬第 N 次 os.replace 失敗；回傳呼叫計數。"""
    import os as _os
    real = _os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == fail_at:
            raise OSError("simulated replace failure")
        return real(src, dst)
    monkeypatch.setattr(_os, "replace", flaky)
    return calls


def _disputed_dir(tmp_path):
    rev = tmp_path / "reviews"
    rev.mkdir()
    _setup_reviews(rev, _annotations(seller_price="5000"),
                   _annotations(seller_price="6000"))
    return rev


def test_adjudication_second_file_write_failure_leaves_no_partial_output(
        tmp_path, monkeypatch):
    # 第一個 commit（final GT）成功、第二個 commit 失敗 → 不得留下半套
    rev = _disputed_dir(tmp_path)
    _flaky_replace(monkeypatch, fail_at=2)  # 無舊檔：commit#2（adjudication）失敗
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c",
                            decision_reason="x",
                            final_ground_truth=_annotations())
    assert not (rev / "adjudication.json").exists(), "不得留下半套 adjudication"
    assert not (rev / "final_ground_truth.json").exists(), \
        "已 commit 的 final GT 也必須 rollback"


def test_adjudication_second_replace_failure_rolls_back_first(
        tmp_path, monkeypatch):
    # 有舊檔：first（adjudication）commit 失敗 → 兩個舊檔都恢復
    rev = _disputed_dir(tmp_path)
    # 先成功寫入一版（建立舊檔）
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    old_adj = (rev / "adjudication.json").read_bytes()
    old_gt = (rev / "final_ground_truth.json").read_bytes()
    # 再寫入一次，第二次 commit 失敗（backup 2 + commit 2 = 第 4 次）
    _flaky_replace(monkeypatch, fail_at=4)
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    assert (rev / "adjudication.json").read_bytes() == old_adj, "adjudication 舊檔恢復"
    assert (rev / "final_ground_truth.json").read_bytes() == old_gt, "final GT 舊檔恢復"


def test_adjudication_first_replace_failure_leaves_no_partial_output(
        tmp_path, monkeypatch):
    # 第一個 commit（final GT）就失敗 → 無任何新檔
    rev = _disputed_dir(tmp_path)
    _flaky_replace(monkeypatch, fail_at=1)
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c",
                            decision_reason="x",
                            final_ground_truth=_annotations())
    assert not (rev / "adjudication.json").exists()
    assert not (rev / "final_ground_truth.json").exists()


def test_existing_adjudication_files_preserved_on_failure(
        tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    old_adj = (rev / "adjudication.json").read_bytes()
    old_gt = (rev / "final_ground_truth.json").read_bytes()
    _flaky_replace(monkeypatch, fail_at=3)  # backup 完成後第一個 commit 失敗
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    assert (rev / "adjudication.json").read_bytes() == old_adj
    assert (rev / "final_ground_truth.json").read_bytes() == old_gt


def test_adjudication_temp_files_cleaned_on_failure(tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    _flaky_replace(monkeypatch, fail_at=1)
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c",
                            decision_reason="x",
                            final_ground_truth=_annotations())
    leftovers = [p for p in rev.iterdir()
                 if ".tmp." in p.name or ".bak." in p.name]
    assert leftovers == [], f"temp/backup 必須清理：{leftovers}"


def test_adjudication_backup_files_cleaned_on_success(tmp_path):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations())
    leftovers = [p for p in rev.iterdir()
                 if ".tmp." in p.name or ".bak." in p.name]
    assert leftovers == [], "成功後不得殘留 temp/backup"


def test_reviewer_files_unchanged_on_partial_failure(tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    before_a = (rev / "reviewer_a.json").read_bytes()
    before_b = (rev / "reviewer_b.json").read_bytes()
    _flaky_replace(monkeypatch, fail_at=2)
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c",
                            decision_reason="x",
                            final_ground_truth=_annotations())
    assert (rev / "reviewer_a.json").read_bytes() == before_a
    assert (rev / "reviewer_b.json").read_bytes() == before_b


def test_dry_run_creates_no_temp_or_backup_files(tmp_path):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="x",
                        final_ground_truth=_annotations(),
                        write_files=False)
    assert not (rev / "adjudication.json").exists()
    assert not (rev / "final_ground_truth.json").exists()
    leftovers = [p for p in rev.iterdir()
                 if ".tmp." in p.name or ".bak." in p.name]
    assert leftovers == [], "dry-run 不得建立 temp/backup"


def test_successful_atomic_write_creates_both_files(tmp_path):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="x",
                        final_ground_truth=_annotations(seller_price="5000"))
    assert (rev / "adjudication.json").exists()
    assert (rev / "final_ground_truth.json").exists()


def test_final_ground_truth_hash_matches_written_canonical_file(tmp_path):
    from alkaid_cs2.evaluation.intake_validation import (
        compute_final_ground_truth_hash,
    )
    rev = _disputed_dir(tmp_path)
    final_gt = _annotations(seller_price="5000")
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="x",
                        final_ground_truth=final_gt)
    written = json.loads((rev / "final_ground_truth.json").read_text(
        encoding="utf-8"))
    record = json.loads((rev / "adjudication.json").read_text(encoding="utf-8"))
    assert compute_final_ground_truth_hash(written) == \
        compute_final_ground_truth_hash(final_gt)
    assert record["final_ground_truth_hash"] == \
        compute_final_ground_truth_hash(final_gt)


# ================================================================
# Phase 6.4C2-A.7 — Rollback failure preserves recovery backups
# ================================================================
def _flaky_replace_seq(monkeypatch, fail_at_set):
    """模擬指定第 N 次 os.replace 失敗（可多點）；回傳呼叫計數。"""
    import os as _os
    real = _os.replace
    calls = {"n": 0}

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] in fail_at_set:
            raise OSError("simulated replace failure")
        return real(src, dst)
    monkeypatch.setattr(_os, "replace", flaky)
    return calls


def test_rollback_failure_preserves_backup_files(tmp_path, monkeypatch):
    # 有舊檔：commit#2 失敗（#4）＋ rollback restore 也失敗（#5）
    # → backup 必須保留（唯一 recovery copy）
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    old_adj = (rev / "adjudication.json").read_bytes()
    old_gt = (rev / "final_ground_truth.json").read_bytes()
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 5})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    backups = [p for p in rev.iterdir() if ".bak." in p.name]
    assert backups, "rollback 失敗後 backup 必須保留供人工復原"
    for bak in backups:
        data = bak.read_bytes()
        assert data == old_adj or data == old_gt, "backup 內容 = 舊版"


def test_rollback_failure_does_not_delete_only_recovery_copy(
        tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 5})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    backups = [p for p in rev.iterdir() if ".bak." in p.name]
    assert backups, "不得刪除唯一 recovery copy"


def test_partial_rollback_preserves_remaining_backup(tmp_path, monkeypatch):
    # 部分 rollback：final GT 恢復成功、adjudication restore 失敗
    # → 尚未恢復的 backup 保留
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 6})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    backups = [p for p in rev.iterdir() if ".bak." in p.name]
    assert backups, "尚未恢復的 backup 必須保留"


def test_rollback_failure_raises_atomic_write_error(tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 5})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))


def test_rollback_failure_cleans_temp_but_keeps_backup(tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 5})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    temps = [p for p in rev.iterdir() if ".tmp." in p.name]
    backups = [p for p in rev.iterdir() if ".bak." in p.name]
    assert temps == [], "temp 必須清理"
    assert backups, "backup 保留"


def test_reviewer_files_unchanged_when_rollback_fails(tmp_path, monkeypatch):
    rev = _disputed_dir(tmp_path)
    adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v1",
                        final_ground_truth=_annotations(seller_price="5000"))
    before_a = (rev / "reviewer_a.json").read_bytes()
    before_b = (rev / "reviewer_b.json").read_bytes()
    _flaky_replace_seq(monkeypatch, fail_at_set={4, 5})
    with pytest.raises(AtomicWriteError):
        adjudicate_disputed(rev, adjudicator="reviewer_c", decision_reason="v2",
                            final_ground_truth=_annotations(seller_price="6000"))
    assert (rev / "reviewer_a.json").read_bytes() == before_a
    assert (rev / "reviewer_b.json").read_bytes() == before_b
