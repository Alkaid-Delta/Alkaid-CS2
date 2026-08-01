"""
test_parse_pipeline.py — parse_post Pipeline 測試（Phase 5）

驗證：完整解析流程、intent/status 推導、warnings、error 策略、無外部呼叫。
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.parsed_post import ParseStatus  # noqa: E402
from alkaid_cs2.domain.raw_post import RawPostInput  # noqa: E402
from alkaid_cs2.pipeline.parse_pipeline import (  # noqa: E402
    derive_parse_status,
    derive_post_intent,
    parse_post,
)

# ── 測試字典 ──
FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {
    "红线": "Redline", "紅線": "Redline", "火神": "Vulcan",
    "鈷分裂": "Cobalt Disruption", "電擊": "Electric Hive", "夜行衣": "Nocts",
}
WEAPON_MAP = {
    "AK-47": "AK-47", "ak": "AK-47", "沙鷹": "Desert Eagle",
    "AWP": "AWP", "爪子刀": "Karambit",
}


def run(text: str, post_id: str = "t1") -> ParseStatus:
    """執行 parse_post 並回傳 ParsedPost（輔助）"""
    post = RawPostInput(post_id=post_id, raw_text=text, source="test")
    return parse_post(
        post,
        full_dict=FULL_DICT,
        pattern_dict=PATTERN_DICT,
        weapon_map=WEAPON_MAP,
    )


# ================================================================
# 1. 單商品單價格
# ================================================================
def test_single_item_single_price():
    p = run("售 AK-47 | 红线 久经沙场 5000")
    assert len(p.items) == 1
    assert len(p.prices) == 1
    assert p.intent is ItemRole.SELLING
    assert p.parse_status is ParseStatus.OK
    # item linked to 5000
    assert p.items[0].linked_price_indexes == [0]
    assert p.prices[0].associated_item_index == 0
    assert p.warnings == [], f"單商品不應有 warning: {p.warnings}"


# ================================================================
# 2. 多商品多價格
# ================================================================
def test_multi_item_multi_price():
    p = run("出2把傳家寶ak 14卡托紅線 7480 火神4xtitan 14000")
    assert len(p.items) == 2
    assert len(p.prices) == 2
    assert p.intent is ItemRole.SELLING
    assert p.parse_status is ParseStatus.OK
    # 兩者正確配對（紅線→7480、火神→14000）
    red = next(it for it in p.items if it.skin == "Redline")
    vul = next(it for it in p.items if it.skin == "Vulcan")
    red_amt = {p.prices[j].money.amount for j in red.linked_price_indexes}
    vul_amt = {p.prices[j].money.amount for j in vul.linked_price_indexes}
    assert Decimal("7480") in red_amt
    assert Decimal("14000") in vul_amt


# ================================================================
# 3. seller ask + BUFF floor + calculated 同一商品
# ================================================================
def test_three_prices_one_item():
    p = run("售 夜行衣 同磨底2100*4.4=9200算5000")
    assert len(p.items) == 1
    assert len(p.prices) == 3
    assert p.parse_status is ParseStatus.OK
    assert len(p.items[0].linked_price_indexes) == 3


# ================================================================
# 4. selling + buying mixed intent
# ================================================================
def test_selling_and_buying_mixed_intent():
    p = run("售 沙鷹鈷分裂 5000。收 AWP 電擊 3000")
    assert p.intent is ItemRole.UNKNOWN
    assert "mixed_intent:selling+buying" in p.warnings
    assert p.parse_status is ParseStatus.OK


# ================================================================
# 5. trade 無價格
# ================================================================
def test_trade_without_price():
    p = run("紅線貼換火神")
    assert p.intent is ItemRole.TRADE
    assert p.prices == []
    assert p.parse_status is ParseStatus.PARTIAL
    assert "no_prices" in p.warnings


# ================================================================
# 6. 有價格無商品
# ================================================================
def test_price_without_item():
    p = run("今天行情不錯 5000")
    assert p.items == []
    assert p.prices != []
    assert p.parse_status is ParseStatus.PARTIAL
    assert "no_items" in p.warnings


# ================================================================
# 7. 有商品無價格
# ================================================================
def test_item_without_price():
    p = run("售 紅線")
    assert p.items != []
    assert p.prices == []
    assert p.parse_status is ParseStatus.PARTIAL
    assert "no_prices" in p.warnings
    assert p.unlinked_item_indexes, "商品應在 unlinked"


# ================================================================
# 8. 完全不相關 → SKIPPED
# ================================================================
def test_unrelated_post_skipped():
    p = run("今天天氣很好")
    assert p.items == []
    assert p.prices == []
    assert p.parse_status is ParseStatus.SKIPPED


# ================================================================
# 9. ambiguous → PARTIAL
# ================================================================
def test_ambiguous_link_partial():
    p = run("紅線 火神 5000")
    assert all(price.associated_item_index is None for price in p.prices)
    assert "ambiguous_links:1" in p.warnings
    assert p.parse_status is ParseStatus.PARTIAL


# ================================================================
# 10. bundle total 延後
# ================================================================
def test_bundle_total_deferred():
    p = run("紅線與火神兩把一起20000")
    assert p.prices and p.prices[0].associated_item_index is None
    assert "bundle_total_deferred:1" in p.warnings
    assert p.parse_status is ParseStatus.PARTIAL
    assert p.intent is not ItemRole.SELLING, "bundle 不得誤判 SELLING"


# ================================================================
# 11. validation_error → UNRESOLVED（直接測 derive_parse_status）
# ================================================================
def test_validation_error_unresolved():
    item = ItemCandidate(
        skin="Fake", role=ItemRole.UNKNOWN, original_text="x",
        verified=False, validation_error="invalid name",
        parser="test", evidence=ItemEvidence.UNKNOWN, confidence=0.3, score=10,
    )
    status = derive_parse_status([item], [], [], [], [])
    assert status is ParseStatus.UNRESOLVED


# ================================================================
# 12. 輸入不被污染
# ================================================================
def test_no_input_mutation():
    post = RawPostInput(post_id="t1", raw_text="售 紅線 5000", source="test",
                        metadata={"a": 1}, image_urls=["https://x"])
    p = parse_post(post, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP)
    # RawPostInput 不變
    assert post.raw_text == "售 紅線 5000"
    assert post.metadata == {"a": 1}
    assert post.image_urls == ["https://x"]
    # ParsedPost 的 metadata/image_urls 獨立
    post.metadata["a"] = 2
    post.image_urls.append("https://y")
    assert p.metadata == {"a": 1}
    assert p.image_urls == ["https://x"]


# ================================================================
# 13-14. warnings 唯一與穩定順序
# ================================================================
def test_warnings_unique():
    p = run("紅線 火神 5000")
    assert len(p.warnings) == len(set(p.warnings)), f"重複 warning: {p.warnings}"


def test_warnings_stable_order():
    w1 = run("售 紅線 5000；火神只展示").warnings
    w2 = run("售 紅線 5000；火神只展示").warnings
    assert w1 == w2
    # 已知順序：unlinked 系列固定
    known = ["no_items", "no_prices", "unlinked_items", "unlinked_prices",
             "ambiguous_links", "bundle_total_deferred"]
    idx = [w1.index(k) for k in w1 if k.split(":")[0] in known]
    assert idx == sorted(idx), f"順序不穩定: {w1}"


# ================================================================
# 15. 非法 post 型別 → raise
# ================================================================
def test_invalid_post_type_raises():
    with pytest.raises(TypeError):
        parse_post("not-a-post", full_dict=FULL_DICT, pattern_dict=PATTERN_DICT,
                   weapon_map=WEAPON_MAP)  # type: ignore[arg-type]


# ================================================================
# 16. 非法字典型別 → raise
# ================================================================
def test_invalid_dict_type_raises():
    post = RawPostInput(post_id="t1", raw_text="售 紅線 5000", source="test")
    with pytest.raises(TypeError):
        parse_post(post, full_dict="x", pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP)  # type: ignore[arg-type]


# ================================================================
# 17. full_dict 優先保留
# ================================================================
def test_full_dict_priority_preserved():
    p = run("AK-47 | 红线")
    assert len(p.items) == 1
    assert p.items[0].evidence is ItemEvidence.DICT_FULL


# ================================================================
# 18. 無外部呼叫（monkeypatch 防禦驗證）
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    p = run("售 紅線 5000")
    assert p.parse_status is ParseStatus.OK


# ================================================================
# 19. parse_status error 策略（方案 B：捕捉 ValueError → ERROR）
# ================================================================
def test_parse_status_error_strategy(monkeypatch):
    import alkaid_cs2.pipeline.parse_pipeline as pp

    def _boom(*a, **k):
        raise ValueError("boom: parse failed")

    monkeypatch.setattr(pp, "parse_item_candidates", _boom)
    post = RawPostInput(post_id="t1", raw_text="售 紅線 5000", source="test")
    p = parse_post(post, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP)
    assert p.parse_status is ParseStatus.ERROR
    assert p.errors and "boom" in p.errors[0]
    assert p.items == [] and p.prices == []


# ================================================================
# 20. 結果欄位完整
# ================================================================
def test_result_fields_complete():
    p = run("售 紅線 5000")
    assert p.post_id == "t1"
    assert isinstance(p.raw_text, str)
    assert isinstance(p.image_urls, list)
    assert isinstance(p.items, list)
    assert isinstance(p.prices, list)
    assert isinstance(p.link_decisions, list)
    assert isinstance(p.parse_status, ParseStatus)
    assert isinstance(p.intent, ItemRole)
    assert isinstance(p.warnings, list)
    assert isinstance(p.errors, list)
    assert isinstance(p.unlinked_item_indexes, list)
    assert isinstance(p.unlinked_price_indexes, list)
    assert p.model_used is None
    assert p.escalation_reason is None
    assert p.source == "test"
    assert isinstance(p.metadata, dict)
