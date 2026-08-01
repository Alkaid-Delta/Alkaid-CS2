"""test_evaluation_models.py — evaluation 資料模型測試（Phase 6.4A）"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.evaluation.models import (  # noqa: E402
    EvaluationCase, EvaluationImage, EvaluationSource, ExpectedImageKind,
    ExpectedItem, parse_decimal,
)


def make_item(**kw):
    base = dict(market_hash_name="AK-47 | Redline (Field-Tested)",
                weapon="AK-47", skin="Redline", wear="Field-Tested",
                stattrak=False, role="selling", seller_price=Decimal("5000"),
                currency=Currency.TWD, image_indexes=[0])
    base.update(kw)
    return ExpectedItem(**base)


def make_image(idx=0, kind="single", item_indexes=(0,), create_price=True):
    return EvaluationImage(
        image_index=idx, image_url=f"fixture://image/{idx}",
        image_kind=ExpectedImageKind(kind),
        vision_payload={"type": "single", "items": []},
        expected_item_indexes=list(item_indexes),
        should_create_price=create_price)


def make_case(**kw):
    base = dict(case_id="case_001", source=EvaluationSource.SYNTHETIC,
                author="anonymous", link="fixture://case_001",
                raw_text="售 紅線 算5000", images=[make_image()],
                expected_items=[make_item()], expected_post_intent="selling",
                expected_safe_for_production=True, tags=["single", "safe"])
    base.update(kw)
    return EvaluationCase(**base)


def test_valid_expected_item():
    it = make_item()
    assert it.market_hash_name == "AK-47 | Redline (Field-Tested)"
    assert it.seller_price == Decimal("5000")


def test_invalid_decimal():
    with pytest.raises(TypeError):
        make_item(seller_price="5000")  # 非 Decimal
    with pytest.raises(ValueError):
        make_item(seller_price=Decimal("NaN"))


def test_negative_price():
    with pytest.raises(ValueError):
        make_item(seller_price=Decimal("-1"))


def test_duplicate_image_indexes():
    with pytest.raises(ValueError):
        make_item(image_indexes=[0, 0])


def test_invalid_image_kind():
    with pytest.raises(TypeError):
        EvaluationImage(image_index=0, image_url="u",
                        image_kind="not-a-kind")


def test_valid_image():
    im = make_image()
    assert im.should_create_price is True


def test_invalid_expected_item_index():
    with pytest.raises(ValueError):
        make_case(images=[make_image(item_indexes=(5,))])  # 超出 expected_items


def test_valid_case():
    c = make_case()
    assert c.case_id == "case_001"
    assert c.expected_safe_for_production is True


def test_duplicate_case_image_index():
    with pytest.raises(ValueError):
        make_case(images=[make_image(0), make_image(0, kind="inventory")])


def test_invalid_case_id():
    with pytest.raises(ValueError):
        make_case(case_id="bad case id!@#")


def test_tags_deduplicated():
    c = make_case(tags=["single", "safe", "single"])
    assert c.tags == ["single", "safe"]


def test_defensive_copy():
    it = make_item(image_indexes=[0])
    it.image_indexes.append(1)
    assert it.image_indexes == [0, 1]
    c = make_case()
    c.images[0].expected_item_indexes.append(9)  # 修改原物件不影響 case 內
    assert make_case().images[0].expected_item_indexes == [0]


def test_prediction_validation():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    p = EvaluationPrediction(case_id="c1", parser_name="legacy")
    assert p.parser_name == "legacy"
    with pytest.raises(ValueError):
        EvaluationPrediction(case_id="c1", parser_name="bogus")
    with pytest.raises(ValueError):
        EvaluationPrediction(case_id="c1", parser_name="legacy",
                             latency_ms=-1)


def test_negative_latency():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError):
        EvaluationPrediction(case_id="c1", parser_name="legacy",
                             latency_ms=-5.0)


def test_bool_count_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(TypeError):
        EvaluationPrediction(case_id="c1", parser_name="legacy",
                             image_count=True)  # type: ignore[arg-type]


def test_parse_decimal_string():
    assert parse_decimal("5000", "x") == Decimal("5000")
    assert parse_decimal(None, "x") is None
    with pytest.raises(ValueError):
        parse_decimal("NaN", "x")


# ================================================================
# Phase 6.4B.3 — Decimal 契約
# ================================================================
def test_parse_decimal_rejects_int():
    with pytest.raises(TypeError):
        parse_decimal(5000, "x")


def test_parse_decimal_rejects_float():
    with pytest.raises(TypeError):
        parse_decimal(5000.5, "x")


def test_parse_decimal_rejects_decimal_object():
    with pytest.raises(TypeError):
        parse_decimal(Decimal("5000"), "x")


def test_parse_decimal_rejects_bool():
    with pytest.raises(TypeError):
        parse_decimal(True, "x")


# ================================================================
# Phase 6.4B.2 — Prediction 對齊驗證
# ================================================================
def test_misaligned_currency_length_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError, match="currencies 長度"):
        EvaluationPrediction(
            case_id="c1", parser_name="legacy",
            market_hash_names=["A"], seller_prices=[Decimal("5000")],
            seller_price_item_indexes=[0], currencies=[],  # 長度不匹配
            price_types=["seller_ask"], price_indexes=[0])


def test_misaligned_price_types_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError, match="price_types 長度"):
        EvaluationPrediction(
            case_id="c1", parser_name="legacy",
            market_hash_names=["A"], seller_prices=[Decimal("5000")],
            seller_price_item_indexes=[0], currencies=[Currency.TWD],
            price_types=[],  # 長度不匹配
            price_indexes=[0])


def test_price_index_out_of_range_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError, match="超出 items 範圍"):
        EvaluationPrediction(
            case_id="c1", parser_name="legacy",
            market_hash_names=["A"], seller_prices=[Decimal("5000")],
            seller_price_item_indexes=[5],  # 超出 items
            currencies=[Currency.TWD], price_types=["seller_ask"],
            price_indexes=[0])


def test_item_to_price_pair_out_of_range_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError, match="超出 items 範圍"):
        EvaluationPrediction(
            case_id="c1", parser_name="legacy",
            market_hash_names=["A"], seller_prices=[Decimal("5000")],
            seller_price_item_indexes=[0], currencies=[Currency.TWD],
            price_types=["seller_ask"], price_indexes=[0],
            item_to_price_pairs=[(3, 0)])  # item index 超出


def test_wear_mismatch_items_length_rejected():
    from alkaid_cs2.evaluation.prediction import EvaluationPrediction
    with pytest.raises(ValueError, match="wear_values 長度"):
        EvaluationPrediction(
            case_id="c1", parser_name="legacy",
            market_hash_names=["A", "B"], wear_values=["Field-Tested"],
            seller_prices=[Decimal("5000")], seller_price_item_indexes=[0],
            currencies=[Currency.TWD], price_types=["seller_ask"],
            price_indexes=[0])
