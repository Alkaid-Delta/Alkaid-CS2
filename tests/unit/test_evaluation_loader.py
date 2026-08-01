"""test_evaluation_loader.py — dataset loader 測試（Phase 6.4A）"""
import json
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.evaluation.dataset_loader import (  # noqa: E402
    load_evaluation_case, load_evaluation_directory,
)
from alkaid_cs2.evaluation.models import (  # noqa: E402
    EvaluationSource, ExpectedImageKind,
)

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "evaluation")


def test_load_single_case():
    c = load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    assert c.case_id == "single_twd_safe_001"
    assert c.source == EvaluationSource.SYNTHETIC
    assert len(c.images) == 1
    assert len(c.expected_items) == 1


def test_load_directory_sorted():
    cases = load_evaluation_directory(FIXTURES)
    ids = [c.case_id for c in cases]
    assert ids == sorted(ids), "穩定檔名排序"
    assert len(cases) >= 25


def test_malformed_json_raises(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON"):
        load_evaluation_case(p)


def test_duplicate_case_id_raises(tmp_path):
    for name in ("a.json", "b.json"):
        (tmp_path / name).write_text(
            json.dumps({"case_id": "dup", "source": "synthetic",
                        "author": "a", "link": "l", "raw_text": "t"}),
            encoding="utf-8")
    with pytest.raises(ValueError, match="重複 case_id"):
        load_evaluation_directory(tmp_path)


def test_decimal_string_loaded():
    c = load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    assert c.expected_items[0].seller_price == Decimal("5000")
    assert c.expected_items[0].currency.value == "TWD"


def test_missing_required_field(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"source": "synthetic", "raw_text": "t"}),
                 encoding="utf-8")
    with pytest.raises(ValueError, match="case_id"):
        load_evaluation_case(p)


def test_invalid_enum(tmp_path):
    p = tmp_path / "x.json"
    p.write_text(json.dumps({"case_id": "x1", "source": "nope",
                             "raw_text": "t"}), encoding="utf-8")
    with pytest.raises(ValueError, match="source"):
        load_evaluation_case(p)


def test_source_mapping():
    c = load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    assert c.source == EvaluationSource.SYNTHETIC
    assert isinstance(c.images[0].image_kind, ExpectedImageKind)


def test_payload_deepcopy():
    c = load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    payload = c.images[0].vision_payload
    assert isinstance(payload, dict)
    payload["items"].append({"name": "HACK"})
    c2 = load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    assert len(c2.images[0].vision_payload["items"]) == 1, "payload 不共享"


def test_fixture_not_mutated():
    before = open(os.path.join(FIXTURES, "single_twd_safe_001.json"),
                  encoding="utf-8").read()
    load_evaluation_case(os.path.join(FIXTURES, "single_twd_safe_001.json"))
    after = open(os.path.join(FIXTURES, "single_twd_safe_001.json"),
                 encoding="utf-8").read()
    assert before == after, "原始 JSON 不被修改"


# ================================================================
# Phase 6.4B.1 — 型別強化
# ================================================================
def _write_case(tmp_path, **kw):
    data = {"case_id": "t1", "source": "synthetic", "author": "a",
            "link": "l", "raw_text": "售 紅線 算5000",
            "expected_safe_for_production": True,
            "expected_items": [{"market_hash_name": "AK-47 | Redline (Field-Tested)",
                                "seller_price": "5000", "currency": "TWD",
                                "image_indexes": [0]}],
            "images": [{"image_index": 0, "image_url": "u",
                        "image_kind": "single"}]}
    data.update(kw)
    p = tmp_path / "t.json"
    p.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return p


def test_loader_rejects_string_bool(tmp_path):
    p = _write_case(tmp_path, expected_safe_for_production="false")
    with pytest.raises(TypeError, match="bool"):
        load_evaluation_case(p)


def test_string_false_not_coerced_to_true(tmp_path):
    p = _write_case(tmp_path, expected_safe_for_production="false")
    with pytest.raises(TypeError):
        load_evaluation_case(p)


def test_int_author_rejected(tmp_path):
    p = _write_case(tmp_path, author=123)
    with pytest.raises(TypeError, match="str"):
        load_evaluation_case(p)


def test_non_string_tag_rejected(tmp_path):
    p = _write_case(tmp_path, tags=["ok", 7])
    with pytest.raises(TypeError, match="str"):
        load_evaluation_case(p)


def test_float_decimal_rejected(tmp_path):
    p = _write_case(tmp_path, expected_items=[
        {"market_hash_name": "A", "seller_price": 5000.5, "currency": "TWD",
         "image_indexes": [0]}])
    with pytest.raises(TypeError, match="字串"):
        load_evaluation_case(p)


def test_raw_safe_wrong_type_rejected(tmp_path):
    p = _write_case(tmp_path, expected_raw_vision_safe="yes")
    with pytest.raises(TypeError, match="bool"):
        load_evaluation_case(p)
