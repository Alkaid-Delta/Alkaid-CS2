"""
test_vision_adapter.py — Vision adapter 測試（Phase 6.3A）

驗證：payload 標準化、價格型別推斷、hallucination 防護、image_index 傳遞。
"""
import sys
import os
import json
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.adapters.vision_adapter import (  # noqa: E402
    normalize_vision_payload,
    vision_payload_to_evidence,
    vision_result_to_evidence,
)
from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.image_evidence import ImageKind, ImagePlatform  # noqa: E402
from alkaid_cs2.domain.item_candidate import ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceSource, PriceType  # noqa: E402
from alkaid_cs2.domain.vision_result import VisionRawResult  # noqa: E402

FIXTURES = os.path.join(PROJECT_ROOT, "tests", "fixtures", "vision")


def load_fixture(name: str):
    with open(os.path.join(FIXTURES, name), encoding="utf-8") as f:
        return json.load(f)


def to_evidence(payload, idx=0, url="https://img/1.jpg"):
    return vision_payload_to_evidence(payload, image_index=idx, image_url=url)


# ================================================================
# 1-5. payload 形式
# ================================================================
def test_single_dict_payload():
    r = normalize_vision_payload({"chinese_name": "AK-47 | 红线", "price": 5000},
                                 image_index=0)
    assert len(r.items) == 1
    assert r.items[0].price_amount == Decimal("5000")


def test_items_wrapper_payload():
    r = normalize_vision_payload({"items": [
        {"chinese_name": "AK-47 | 红线"},
        {"chinese_name": "AK-47 | 火神"},
    ]}, image_index=0)
    assert len(r.items) == 2


def test_list_payload():
    r = normalize_vision_payload([
        {"chinese_name": "A"}, {"chinese_name": "B"}, {"chinese_name": "C"},
    ], image_index=0)
    assert len(r.items) == 3
    assert r.image_kind is ImageKind.MULTI_ITEM


def test_json_string_payload():
    r = normalize_vision_payload('{"chinese_name": "AK-47 | 红线", "price": 5000}',
                                 image_index=0)
    assert len(r.items) == 1


def test_markdown_json_payload():
    payload = '```json\n{"chinese_name": "AK-47 | 红线", "price": 5000}\n```'
    r = normalize_vision_payload(payload, image_index=0)
    assert len(r.items) == 1


# ================================================================
# 6-8. 錯誤 payload
# ================================================================
def test_invalid_json_returns_error():
    r = normalize_vision_payload("{not-json", image_index=0)
    assert "invalid_json" in r.errors
    assert r.items == []


def test_none_payload_returns_error():
    r = normalize_vision_payload(None, image_index=0)
    assert "payload_is_none" in r.errors


def test_malformed_item_skipped():
    r = normalize_vision_payload(load_fixture("malformed_payload.json"), image_index=0)
    # 3 筆：1 有效 + 1 非 dict（skip）+ 1 price 非數字（price_amount=None 但仍建 item）
    assert len(r.items) >= 1
    assert any("item[1]_not_dict_skipped" in w for w in r.warnings)


# ================================================================
# 9-10. 多商品保留 / 單商品候選
# ================================================================
def test_multi_item_preserved():
    ev = to_evidence(load_fixture("multi_item.json"))
    assert len(ev.item_candidates) == 2, "不得只保留第一件"


def test_single_item_candidate_created():
    ev = to_evidence(load_fixture("single_item_twd.json"))
    assert len(ev.item_candidates) == 1
    assert ev.item_candidates[0].evidence is ItemEvidence.VISION
    assert ev.item_candidates[0].parser == "vision_adapter"
    assert ev.item_candidates[0].verified is False


# ================================================================
# 11. 缺 weapon 低信心
# ================================================================
def test_item_without_weapon_low_confidence():
    payload = {"chinese_name": "红线", "price": 5000, "confidence": 0.95}
    ev = to_evidence(payload)
    ic = ev.item_candidates[0]
    assert ic.confidence <= 0.60, f"缺 weapon 信心應 ≤0.60: {ic.confidence}"


# ================================================================
# 12-17. 價格型別推斷
# ================================================================
def test_market_listing_price_is_reference():
    ev = to_evidence(load_fixture("buff_listing.json"))
    pc = ev.price_candidates[0]
    assert pc.price_type is PriceType.REFERENCE, "掛牌價不得當 seller ask"
    assert pc.source is PriceSource.MARKET_SCREENSHOT


def test_buff_floor_price():
    payload = {"type": "single",
               "chinese_name": "AK-47 | 红线", "price": 2100,
               "evidence": "同磨底2100"}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.BUFF_FLOOR


def test_chat_seller_ask():
    ev = to_evidence(load_fixture("chat_seller_price.json"))
    pc = ev.price_candidates[0]
    assert pc.price_type is PriceType.SELLER_ASK
    assert pc.source is PriceSource.CHAT


def test_calculation_price():
    payload = {"type": "single", "chinese_name": "AK-47 | 红线",
               "price": 9200, "evidence": "2100*4.4=9200"}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.CALCULATED


def test_bundle_total_price():
    payload = {"type": "single", "chinese_name": "AK-47 | 红线",
               "price": 20000, "evidence": "兩把一起20000"}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.BUNDLE_TOTAL


def test_unknown_price_type():
    payload = {"type": "single", "chinese_name": "AK-47 | 红线", "price": 5000}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.UNKNOWN


# ================================================================
# 18-20. 貨幣
# ================================================================
def test_rmb_preserved_not_converted():
    ev = to_evidence(load_fixture("buff_listing.json"))
    pc = ev.price_candidates[0]
    assert pc.money.currency is Currency.RMB
    assert pc.money.amount == Decimal("543"), "不得 ×4.5 或 ×7.2"


def test_twd_preserved():
    ev = to_evidence(load_fixture("single_item_twd.json"))
    assert ev.price_candidates[0].money.currency is Currency.TWD


def test_unknown_currency_warning():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "currency": "XYZ币"}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].currency is Currency.UNKNOWN
    assert "unknown_currency" in r.warnings


# ================================================================
# 21-23. 圖片類型規則
# ================================================================
def test_inventory_grid_deferred():
    ev = to_evidence(load_fixture("inventory_grid.json"))
    assert ev.item_candidates == [], "inventory grid 不進交易候選"
    assert "inventory_grid_deferred" in ev.warnings


def test_payment_proof_no_candidates():
    payload = {"type": "payment", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 5000}]}
    ev = to_evidence(payload)
    assert ev.item_candidates == []
    assert ev.price_candidates == []


def test_inspect_screenshot_no_seller_ask():
    ev = to_evidence(load_fixture("inspect_no_price.json"))
    assert len(ev.item_candidates) == 1, "inspect 商品可保留"
    assert ev.price_candidates == [], "inspect 不產價格"


# ================================================================
# 24-28. hallucination 防護
# ================================================================
def test_low_confidence_warning():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": 0.3}
    ev = to_evidence(payload)
    assert "low_confidence" in ev.warnings


def test_name_component_conflict():
    payload = {
        "chinese_name": "AK-47 | 红线",
        "market_hash_name": "AWP | Dragon Lore (Factory New)",
        "weapon": "AK-47",
        "skin": "Redline",
        "confidence": 0.9,
    }
    ev = to_evidence(payload)
    assert "name_component_conflict" in ev.warnings


def test_zero_price_not_created():
    payload = {"chinese_name": "AK-47 | 红线", "price": 0}
    ev = to_evidence(payload)
    assert ev.price_candidates == []


def test_negative_price_not_created():
    payload = {"chinese_name": "AK-47 | 红线", "price": -100}
    ev = to_evidence(payload)
    assert ev.price_candidates == []


def test_suspicious_large_price_warning():
    payload = {"chinese_name": "AK-47 | 红线", "price": 99999999}
    ev = to_evidence(payload)
    assert ev.price_candidates, "不刪除，只 warning"
    assert "suspicious_price_range" in ev.warnings


# ================================================================
# 29. image_index 傳遞
# ================================================================
def test_image_index_written_to_candidates():
    ev = to_evidence(load_fixture("multi_item.json"), idx=3)
    for ic in ev.item_candidates:
        assert ic.image_index == 3
    for pc in ev.price_candidates:
        assert pc.image_index == 3
    assert ev.image_index == 3


# ================================================================
# 30. 無外部呼叫
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    ev = to_evidence(load_fixture("single_item_twd.json"))
    assert len(ev.item_candidates) == 1


# ================================================================
# Phase 6.3A.1 — Safety Hardening
# ================================================================
# 31-33. 非交易圖片即使有 price 也不建 PriceCandidate
# ---------------------------------------------------------------
def test_inspect_payload_with_price_creates_no_price():
    payload = {"type": "inspect", "items": [
        {"chinese_name": "AK-47 | 红线", "wear": "久经沙场", "price": 5000,
         "evidence": "售 5000", "confidence": 0.9}]}
    ev = to_evidence(payload)
    assert len(ev.item_candidates) == 1, "inspect 可保留 item"
    assert ev.price_candidates == [], "inspect 即使有 price 也不建價格"


def test_trade_confirmation_with_price_creates_no_price():
    payload = {"type": "trade", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 5000, "evidence": "售 5000",
         "confidence": 0.9}]}
    ev = to_evidence(payload)
    assert ev.price_candidates == [], "trade confirmation 不建價格"
    assert len(ev.item_candidates) == 1, "trade confirmation 可保守保留 item"


def test_inventory_with_price_creates_no_price():
    payload = {"type": "inventory", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 5000, "evidence": "售 5000",
         "confidence": 0.9}]}
    ev = to_evidence(payload)
    assert ev.price_candidates == []
    assert ev.item_candidates == []
    assert "inventory_grid_deferred" in ev.warnings


# ---------------------------------------------------------------
# 34-38. confidence coercion
# ---------------------------------------------------------------
def test_invalid_confidence_string_becomes_zero():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": "high"}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].confidence == 0.0, "非法字串 → 0.0（不得用預設值）"
    assert "invalid_confidence" in r.warnings


def test_confidence_nan_becomes_zero():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": float("nan")}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].confidence == 0.0
    assert "invalid_confidence" in r.warnings


def test_confidence_inf_becomes_zero():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": float("inf")}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].confidence == 0.0
    assert "invalid_confidence" in r.warnings


def test_explicit_zero_confidence_remains_zero():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": 0.0}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].confidence == 0.0, "合法 0.0 不得被提升"


def test_missing_confidence_uses_default():
    payload = {"chinese_name": "AK-47 | 红线", "price": 5000}
    r = normalize_vision_payload(payload, image_index=0)
    assert r.items[0].confidence == 0.70, "SINGLE 未提供 → 基礎 0.70"
    assert "invalid_confidence" not in r.warnings


# ---------------------------------------------------------------
# 39-40. MARKET_LISTING 價格語意
# ---------------------------------------------------------------
def test_market_listing_sale_label_is_reference():
    payload = {"type": "market", "platform": "buff", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 543, "currency": "RMB",
         "evidence": "售價 543", "confidence": 0.8}]}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.REFERENCE, \
        "掛牌頁含『售價』也不得判 SELLER_ASK"


def test_buff_lowest_price_is_buff_floor_or_reference():
    payload = {"type": "market", "platform": "buff", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 543, "currency": "RMB",
         "evidence": "最低價 543", "confidence": 0.8}]}
    ev = to_evidence(payload)
    assert ev.price_candidates[0].price_type is PriceType.BUFF_FLOOR


# ---------------------------------------------------------------
# 41-44. VisionRawItem 領域驗證
# ---------------------------------------------------------------
def test_stattrak_wrong_type_raises():
    from alkaid_cs2.domain.vision_result import VisionRawItem
    with pytest.raises(TypeError):
        VisionRawItem(stattrak="yes")  # type: ignore[arg-type]


def test_price_amount_nan_raises():
    from alkaid_cs2.domain.vision_result import VisionRawItem
    from decimal import Decimal
    with pytest.raises(ValueError):
        VisionRawItem(price_amount=Decimal("NaN"))


def test_bbox_bool_raises():
    from alkaid_cs2.domain.vision_result import VisionRawItem
    with pytest.raises(ValueError):
        VisionRawItem(bbox=(True, 0, 10, 10))  # type: ignore[arg-type]


def test_bbox_invalid_order_raises():
    from alkaid_cs2.domain.vision_result import VisionRawItem
    with pytest.raises(ValueError):
        VisionRawItem(bbox=(10, 10, 5, 20))  # x2 < x1


# ---------------------------------------------------------------
# 45-46. 巢狀 payload 深拷貝
# ---------------------------------------------------------------
def test_nested_raw_payload_is_deep_copied():
    payload = {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                            "stickers": [{"name": "A"}]}]}
    r = normalize_vision_payload(payload, image_index=0)
    payload["items"][0]["stickers"][0]["name"] = "mutated"  # 外部修改巢狀
    assert r.raw_payload["items"][0]["stickers"][0]["name"] == "A"


def test_nested_raw_result_is_deep_copied():
    payload = {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                            "stickers": [{"name": "B"}]}]}
    ev = to_evidence(payload)
    payload["items"][0]["stickers"][0]["name"] = "mutated"
    assert ev.raw_result["items"][0]["stickers"][0]["name"] == "B"
