"""
test_evidence_merger.py — 文字與圖片證據合併測試（Phase 6.3B）
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.adapters.vision_adapter import vision_payload_to_evidence  # noqa: E402
from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.parsed_post import ParseStatus  # noqa: E402
from alkaid_cs2.domain.raw_post import RawPostInput  # noqa: E402
from alkaid_cs2.pipeline.parse_pipeline import parse_post  # noqa: E402
from alkaid_cs2.services.evidence_merger import merge_text_and_image_evidence  # noqa: E402

FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {"红线": "Redline", "紅線": "Redline", "火神": "Vulcan", "夜行衣": "Nocts"}
WEAPON_MAP = {"AK-47": "AK-47", "ak": "AK-47", "AWP": "AWP"}


def text_post(text: str) -> ParseStatus:
    return parse_post(
        RawPostInput(post_id="p1", raw_text=text, source="test"),
        full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP,
    )


def img_evidence(payload: dict, idx: int = 0, url: str = "https://img/1.jpg"):
    return vision_payload_to_evidence(payload, image_index=idx, image_url=url)


def merge(text: str, payloads: list[dict]):
    post = text_post(text)
    evs = [img_evidence(p, i, f"https://img/{i}.jpg") for i, p in enumerate(payloads)]
    return merge_text_and_image_evidence(post, evs)


# ================================================================
# 1. 文字與圖片相同商品、相同價格 → 合併
# ================================================================
def test_same_item_same_price_merged():
    r = merge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5000"}]}])
    assert r.merged_item_count == 1, f"應合併為 1 item: {r.merged_item_count}"
    assert r.merged_price_count == 1, "相同價格應合併為 1"
    assert any(w.startswith("corroborated_by_image") for w in r.warnings)
    assert r.parsed_post.parse_status is ParseStatus.OK


# ================================================================
# 2. 文字商品、圖片補磨損
# ================================================================
def test_text_item_image_adds_wear():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "TWD", "confidence": 0.5}]}])
    item = r.parsed_post.items[0]
    assert item.wear == "Field-Tested", f"圖片應補磨損: {item.wear}"
    # text pattern 無武器命中 confidence=0.60；合併取 max，不得低於原值
    assert item.confidence >= 0.60, f"不得降低 confidence: {item.confidence}"


# ================================================================
# 3. 文字無商品、圖片有單商品 → PARTIAL
# ================================================================
def test_image_only_item_partial():
    r = merge("今天天氣很好", [{
        "type": "single", "items": [{
            "chinese_name": "AK-47 | 红线", "price": 5000,
            "currency": "TWD", "confidence": 0.8}]}])
    assert r.merged_item_count == 1
    assert any(w.startswith("image_only_item") for w in r.warnings)
    assert r.parsed_post.parse_status is ParseStatus.PARTIAL


# ================================================================
# 4. 兩張圖片各有不同商品 → 全保留
# ================================================================
def test_two_images_two_items_preserved():
    r = merge("今天天氣很好", [
        {"type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                      "price": 7480, "confidence": 0.8}]},
        {"type": "single", "items": [{"chinese_name": "AK-47 | 火神",
                                      "price": 14000, "confidence": 0.8}]},
    ])
    assert r.merged_item_count == 2, "兩張圖兩件商品都保留"


# ================================================================
# 5. 重複圖片 → 只合併一次
# ================================================================
def test_duplicate_image_removed():
    payload = {"type": "single", "items": [{"market_hash_name": "Redline",
                                            "chinese_name": "AK-47 | 红线",
                                            "price": 5000, "confidence": 0.8}]}
    ev1 = img_evidence(payload, 0, "https://same/1.jpg")
    ev2 = img_evidence(payload, 1, "https://same/1.jpg")
    post = text_post("售 紅線 算5000")
    r = merge_text_and_image_evidence(post, [ev1, ev2])
    assert r.merged_item_count == 1
    assert any(w.startswith("duplicate_images_removed") for w in r.warnings)


# ================================================================
# 6. wear conflict → 兩候選保留
# ================================================================
def test_wear_conflict():
    r = merge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "AK-47 | Redline (Minimal Wear)",
            "weapon": "AK-47", "skin": "Redline", "wear": "略有磨损",
            "price": 5000, "currency": "TWD", "confidence": 0.9}]}])
    assert r.merged_item_count == 2, "wear 衝突不得自動合併"
    assert any(c.conflict_type.value == "wear_conflict" for c in r.conflicts)
    assert r.parsed_post.parse_status is ParseStatus.PARTIAL


# ================================================================
# 7. seller price conflict → 兩筆保留 + error
# ================================================================
def test_seller_price_conflict():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5500, "currency": "TWD", "confidence": 0.9,
            "evidence": "售 5500"}]}])
    assert r.merged_price_count == 2, "不同賣價不得硬選"
    pc = [c for c in r.conflicts if c.conflict_type.value == "price_conflict"]
    assert pc and pc[0].severity == "error"
    assert r.parsed_post.parse_status is ParseStatus.PARTIAL


# ================================================================
# 8. BUFF floor + seller ask 共存（不衝突、不換算）
# ================================================================
def test_buff_floor_not_conflict_with_seller_ask():
    r = merge("售 紅線 算5000", [{
        "type": "market", "platform": "buff", "items": [{
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 4300, "currency": "RMB", "confidence": 0.8,
            "evidence": "BUFF 最低價 4300"}]}])
    assert r.merged_price_count == 2, "seller ask + buff floor 共存"
    assert not any(c.conflict_type.value == "price_conflict" for c in r.conflicts)
    # 不換算：RMB 原樣
    rmb = [p for p in r.parsed_post.prices if p.money.currency is Currency.RMB]
    assert rmb and rmb[0].money.amount == Decimal("4300")


# ================================================================
# 9. currency conflict
# ================================================================
def test_currency_conflict():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{
            "market_hash_name": "Redline",
            "chinese_name": "AK-47 | 红线", "wear": "久经沙场",
            "price": 5000, "currency": "RMB", "confidence": 0.9,
            "evidence": "售 5000 RMB"}]}])
    cc = [c for c in r.conflicts if c.conflict_type.value == "currency_conflict"]
    assert cc and cc[0].severity == "error"
    assert r.merged_price_count == 2


# ================================================================
# 10. 一張圖 1 item + 多 prices → 全綁
# ================================================================
def test_one_image_one_item_multi_prices_linked():
    r = merge("今天天氣很好", [{
        "type": "single", "items": [{
            "chinese_name": "AK-47 | 红线", "price": 5000, "currency": "TWD",
            "confidence": 0.8, "evidence": "售 5000"}]}])
    # 只有 1 price；改測 1 item + 多 price 情境
    payload = {"type": "single", "items": [{
        "chinese_name": "AK-47 | 红线",
        "price": 5000, "currency": "TWD", "confidence": 0.8, "evidence": "售 5000"}]}
    ev = img_evidence(payload, 0)
    # 手動附加第二、三個 price candidates
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType
    ev.price_candidates.append(PriceCandidate(
        money=Money(Decimal("2100"), Currency.RMB), price_type=PriceType.BUFF_FLOOR,
        source=PriceSource.IMAGE, evidence="同磨底2100", confidence=0.7,
        image_index=0))
    ev.price_candidates.append(PriceCandidate(
        money=Money(Decimal("9200"), Currency.TWD), price_type=PriceType.CALCULATED,
        source=PriceSource.IMAGE, evidence="2100*4.4=9200", confidence=0.7,
        image_index=0))
    post = text_post("今天天氣很好")
    r = merge_text_and_image_evidence(post, [ev])
    assert r.merged_price_count == 3
    item = r.parsed_post.items[0]
    assert len(item.linked_price_indexes) == 3, "1 item 3 prices 全綁"


# ================================================================
# 11. 數量相等 → 按順序關聯
# ================================================================
def test_equal_count_order_linking():
    r = merge("今天天氣很好", [{
        "type": "multi", "items": [
            {"chinese_name": "AK-47 | 红线", "skin": "Redline",
             "price": 7480, "currency": "TWD", "confidence": 0.8},
            {"chinese_name": "AK-47 | 火神", "skin": "Vulcan",
             "price": 14000, "currency": "TWD", "confidence": 0.8},
        ]}])
    assert any(w.startswith("image_order_linking") for w in r.warnings)
    items = r.parsed_post.items
    red = next(i for i in items if i.skin == "Redline")
    vul = next(i for i in items if i.skin == "Vulcan")
    red_amt = {r.parsed_post.prices[j].money.amount for j in red.linked_price_indexes}
    vul_amt = {r.parsed_post.prices[j].money.amount for j in vul.linked_price_indexes}
    assert Decimal("7480") in red_amt, f"Redline 應綁 7480: {red_amt}"
    assert Decimal("14000") in vul_amt, f"Vulcan 應綁 14000: {vul_amt}"


# ================================================================
# 12. 多商品多價格模糊 → AMBIGUOUS_LINK 不硬配
# ================================================================
def test_ambiguous_multi_item_multi_price():
    r = merge("今天天氣很好", [{
        "type": "multi", "items": [
            {"chinese_name": "AK-47 | 红线", "price": 5000, "currency": "TWD",
             "confidence": 0.8},
            {"chinese_name": "AK-47 | 火神", "price": 14000, "currency": "TWD",
             "confidence": 0.8},
        ]}])
    # 2 items + 2 prices = N:N 其實可順序配……改成 2 items + 1 price 才模糊
    payload = {"type": "multi", "items": [
        {"chinese_name": "AK-47 | 红线", "confidence": 0.8},
        {"chinese_name": "AK-47 | 火神", "price": 14000, "currency": "TWD",
         "confidence": 0.8},
    ]}
    ev = img_evidence(payload, 0)
    post = text_post("今天天氣很好")
    r2 = merge_text_and_image_evidence(post, [ev])
    assert any(c.conflict_type.value == "ambiguous_link" for c in r2.conflicts)
    assert any(p.associated_item_index is None for p in r2.parsed_post.prices)


# ================================================================
# 13-14. 輸入不被修改
# ================================================================
def test_text_input_not_mutated():
    post = text_post("售 紅線 算5000")
    orig_items = list(post.items)
    orig_prices = list(post.prices)
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "wear": "久经沙场", "price": 5000,
                                     "currency": "TWD", "confidence": 0.9}]}])
    assert post.items == orig_items
    assert post.prices == orig_prices
    assert r.parsed_post is not post


def test_image_input_not_mutated():
    ev = img_evidence({"type": "single", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": 0.8}]})
    orig_count = len(ev.item_candidates)
    post = text_post("售 紅線 算5000")
    merge_text_and_image_evidence(post, [ev])
    assert len(ev.item_candidates) == orig_count
    assert ev.item_candidates[0].linked_price_indexes == []


# ================================================================
# 15-16. warnings 唯一與順序
# ================================================================
def test_warnings_unique():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "currency": "TWD",
                                     "confidence": 0.9}]}])
    assert len(r.warnings) == len(set(r.warnings))


def test_stable_order():
    r1 = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.9}]}])
    r2 = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.9}]}])
    assert r1.warnings == r2.warnings


# ================================================================
# 17. metadata 更新
# ================================================================
def test_metadata_updated():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    m = r.parsed_post.metadata
    assert m["image_evidence_count"] == 1
    assert m["image_candidate_count"] == 1
    assert m["vision_merged"] is True
    assert "merge_conflict_count" in m


# ================================================================
# 18. error conflict → escalation_reason
# ================================================================
def test_escalation_reason_on_error_conflict():
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"market_hash_name": "Redline",
                                     "chinese_name": "AK-47 | 红线",
                                     "wear": "久经沙场", "price": 5500,
                                     "currency": "TWD", "confidence": 0.9,
                                     "evidence": "售 5500"}]}])
    assert r.parsed_post.escalation_reason == "vision_merge_conflict"


# ================================================================
# 19. 原 ERROR status 保留
# ================================================================
def test_original_error_status_preserved():
    from alkaid_cs2.domain.parsed_post import ParsedPost
    post = ParsedPost(post_id="p1", raw_text="x", parse_status=ParseStatus.ERROR,
                      errors=["boom"], source="test")
    ev = img_evidence({"type": "single", "items": [
        {"chinese_name": "AK-47 | 红线", "price": 5000, "confidence": 0.8}]})
    r = merge_text_and_image_evidence(post, [ev])
    assert r.parsed_post.parse_status is ParseStatus.ERROR


# ================================================================
# 20. 無外部呼叫
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    r = merge("售 紅線 算5000", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    assert r.merged_item_count >= 1


# ================================================================
# 21. 不換算貨幣
# ================================================================
def test_no_currency_conversion():
    r = merge("售 紅線 算5000", [{
        "type": "market", "platform": "steam", "items": [
            {"chinese_name": "AK-47 | 红线", "price": 320, "currency": "USD",
             "confidence": 0.8, "evidence": "Steam $320"}]}])
    usd = [p for p in r.parsed_post.prices if p.money.currency is Currency.USD]
    assert usd and usd[0].money.amount == Decimal("320"), "USD 不得換算"


# ================================================================
# 22. image_index 保留
# ================================================================
def test_image_index_preserved():
    r = merge("今天天氣很好", [{
        "type": "single", "items": [{"chinese_name": "AK-47 | 红线",
                                     "price": 5000, "confidence": 0.8}]}])
    for it in r.parsed_post.items:
        assert it.image_index == 0


# ================================================================
# 23-24. 索引雙向一致
# ================================================================
def test_linked_indexes_consistent():
    r = merge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{"market_hash_name": "AK-47 | Redline (Field-Tested)",
                                     "chinese_name": "AK-47 | 红线",
                                     "price": 5000, "currency": "TWD",
                                     "confidence": 0.9, "evidence": "售 5000"}]}])
    for it in r.parsed_post.items:
        for j in it.linked_price_indexes:
            assert r.parsed_post.prices[j].associated_item_index == \
                r.parsed_post.items.index(it)


def test_associated_item_index_consistent():
    r = merge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{"market_hash_name": "AK-47 | Redline (Field-Tested)",
                                     "chinese_name": "AK-47 | 红线",
                                     "price": 5000, "currency": "TWD",
                                     "confidence": 0.9, "evidence": "售 5000"}]}])
    for pi, p in enumerate(r.parsed_post.prices):
        if p.associated_item_index is not None:
            assert pi in r.parsed_post.items[p.associated_item_index].linked_price_indexes


# ================================================================
# 25. merged counts 正確
# ================================================================
def test_merged_counts_correct():
    r = merge("售 AK-47 | 红线 久经沙场 算5000", [{
        "type": "single", "items": [{"market_hash_name": "AK-47 | Redline (Field-Tested)",
                                     "chinese_name": "AK-47 | 红线",
                                     "price": 5000, "currency": "TWD",
                                     "confidence": 0.9, "evidence": "售 5000"}]}])
    assert r.text_item_count == 1
    assert r.image_item_count == 1
    assert r.merged_item_count == 1
    assert r.text_price_count == 1
    assert r.image_price_count == 1
    assert r.merged_price_count == 1
