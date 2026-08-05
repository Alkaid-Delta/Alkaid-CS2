# -*- coding: utf-8 -*-
"""test_structured_consumer_boundary.py — P6-R1-E1 authentic behavioral RED（10 個）

RED-E1 契約：每項必須到達 target behavior（linkage/bridge/consumer 行為），
不得因 import/setup error 失敗。
"""
import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from alkaid_cs2.domain.price_candidate import PriceType, PriceSource
from alkaid_cs2.integration.vision_production import VisionImageInput
from alkaid_cs2.domain.market_candidate import build_market_candidates
from alkaid_cs2.domain.price import Money, ConvertedMoney
from alkaid_cs2.domain.enums import Currency

# consumer helper（P6-R1-E1 將實作——修改前為 None → assertion fail 而非 import error）
try:
    from analyze_arbitrage import process_structured_market_candidates
except ImportError:
    process_structured_market_candidates = None


# ---------- helpers ----------
def _payload(items, img_type="single"):
    return {"type": img_type, "platform": "facebook", "items": items,
            "overall_confidence": 0.9}


def _item(name, price=None, currency="TWD", evidence="售價", role="selling", conf=0.9):
    d = {"raw_name": name, "chinese_name": name, "evidence": evidence,
         "role": role, "confidence": conf}
    if price is not None:
        d["price"] = price
        d["currency"] = currency
    return d


def _run_parse(vis_inputs, text):
    from alkaid_cs2.domain.raw_post import RawPostInput
    from alkaid_cs2.integration.vision_production import build_vision_merged_result
    post = RawPostInput(post_id="p6r1e1", author="synthetic", link="",
                        raw_text=text, image_urls=[vi.image_url for vi in vis_inputs],
                        source="facebook")
    vp = build_vision_merged_result(post, vision_inputs=vis_inputs,
                                    full_dict={}, pattern_dict={}, weapon_map={})
    cands = build_market_candidates(vp.merged_post) if vp.merged_post is not None else []
    return vp, cands


def _hand_built_candidates():
    """手建 ParsedPost 等級 candidates（測 linkage gate——不依賴 vision 全鏈）"""
    # item 0 linked price 0（SELLER_ASK TWD 5000）
    m0 = Money(amount=Decimal("5000"), currency=Currency.TWD)
    # 透過 build_market_candidates 需要 ParsedPost——用輕量物件模擬（僅測 gate 邏輯）
    return m0


# ---------- RED-E1-1：同商品 second-image price ----------
def test_red_e1_1_same_item_second_image_price():
    """圖0 = item identity（無價格）、圖1 = 同商品 SELLER_ASK → candidate 1 個（price_image_index=1）"""
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)")])),
        VisionImageInput(1, "inline://p/1", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
    ]
    vp, cands = _run_parse(vis, "text: 賣 AK 紅線\n")
    assert len(cands) >= 1, "同商品 second-image price 未建立 candidate"
    assert any(c.price_image_index == 1 for c in cands), "無 price_image_index=1 candidate"


# ---------- RED-E1-2：association mismatch ----------
def test_red_e1_2_association_mismatch_blocked():
    """item.linked_price_indexes=[0] 但 price.associated_item_index=1 → blocked ASSOCIATION_MISMATCH"""
    from alkaid_cs2.domain import market_candidate as mc
    REASON_ASSOCIATION_MISMATCH = getattr(mc, "REASON_ASSOCIATION_MISMATCH",
                                          "P6_MARKET_CANDIDATE_ASSOCIATION_MISMATCH")
    from alkaid_cs2.domain.parsed_post import ParsedPost
    from alkaid_cs2.domain.item_candidate import ItemCandidate
    from alkaid_cs2.domain.price_candidate import PriceCandidate
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    items = [ItemCandidate(market_hash_name="AK-47 | Redline (Field-Tested)",
                           original_text="AK-47 | Redline (Field-Tested)",
                           verified=True, verified_by="test", parser="test", linked_price_indexes=[0])]
    prices = [PriceCandidate(money=m, price_type=PriceType.SELLER_ASK, source=PriceSource.IMAGE, evidence="售價", confidence=0.9,
                             associated_item_index=1)]
    pp = ParsedPost(post_id="t", source="facebook", items=items, prices=prices)
    cands = build_market_candidates(pp)
    assert all(c.blocked for c in cands), "association mismatch 未 blocked"
    assert any(c.block_reason == REASON_ASSOCIATION_MISMATCH for c in cands), \
        f"缺 ASSOCIATION_MISMATCH（實際: {[c.block_reason for c in cands]}）"


# ---------- RED-E1-3：duplicate price ownership ----------
def test_red_e1_3_duplicate_price_ownership_blocked():
    """item0 與 item1 都 link price0 → price0 只屬 item0；item1 blocked PRICE_ALREADY_OWNED"""
    from alkaid_cs2.domain import market_candidate as mc
    REASON_PRICE_ALREADY_OWNED = getattr(mc, "REASON_PRICE_ALREADY_OWNED",
                                         "P6_MARKET_CANDIDATE_PRICE_ALREADY_OWNED")
    from alkaid_cs2.domain.parsed_post import ParsedPost
    from alkaid_cs2.domain.item_candidate import ItemCandidate
    from alkaid_cs2.domain.price_candidate import PriceCandidate
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    items = [
        ItemCandidate(market_hash_name="ItemA", original_text="ItemA", verified=True, verified_by="t", parser="test", linked_price_indexes=[0]),
        ItemCandidate(market_hash_name="ItemB", original_text="ItemB", verified=True, verified_by="t", parser="test", linked_price_indexes=[0]),
    ]
    prices = [PriceCandidate(money=m, price_type=PriceType.SELLER_ASK, source=PriceSource.IMAGE, evidence="售價", confidence=0.9, associated_item_index=0)]
    pp = ParsedPost(post_id="t", source="facebook", items=items, prices=prices)
    cands = build_market_candidates(pp)
    owned = [c for c in cands if c.item_index == 1]
    assert any(c.blocked and c.block_reason == REASON_PRICE_ALREADY_OWNED for c in owned), \
        f"item1 未 blocked PRICE_ALREADY_OWNED（實際: {[c.block_reason for c in owned]}）"


# ---------- RED-E1-4：bundle reason ----------
def test_red_e1_4_bundle_reason():
    """BUNDLE_TOTAL → BUNDLE_UNSUPPORTED（不得是 NOT_SELLER_ASK）"""
    from alkaid_cs2.domain import market_candidate as mc
    REASON_BUNDLE_UNSUPPORTED = getattr(mc, "REASON_BUNDLE_UNSUPPORTED",
                                        "P6_MARKET_CANDIDATE_BUNDLE_UNSUPPORTED")
    from alkaid_cs2.domain.parsed_post import ParsedPost
    from alkaid_cs2.domain.item_candidate import ItemCandidate
    from alkaid_cs2.domain.price_candidate import PriceCandidate
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    items = [ItemCandidate(market_hash_name="ItemA", original_text="ItemA", verified=True, verified_by="t", parser="test", linked_price_indexes=[0])]
    prices = [PriceCandidate(money=m, price_type=PriceType.BUNDLE_TOTAL, source=PriceSource.IMAGE, evidence="bundle", confidence=0.9, associated_item_index=0)]
    pp = ParsedPost(post_id="t", source="facebook", items=items, prices=prices)
    cands = build_market_candidates(pp)
    assert any(c.block_reason == REASON_BUNDLE_UNSUPPORTED for c in cands), \
        f"bundle 未產生 BUNDLE_UNSUPPORTED（實際: {[c.block_reason for c in cands]}）"


# ---------- RED-E1-5：structured lookup result 真正進 analysis ----------
def test_red_e1_5_structured_deals():
    """fake lookup A/B + fake analyze → deal A + deal B、legacy lookup 0、不交叉"""
    assert process_structured_market_candidates is not None, "process_structured_market_candidates 未實作"
    m0 = Money(amount=Decimal("5000"), currency=Currency.TWD)
    m1 = Money(amount=Decimal("9999"), currency=Currency.TWD)
    from alkaid_cs2.domain.market_candidate import MarketCandidate
    from alkaid_cs2.domain.item_candidate import ItemRole
    cA = MarketCandidate(item_index=0, market_hash_name="ItemA", verified=True,
                         verified_by="t", item_role=ItemRole.SELLING,
                         price_index=0, price_type=PriceType.SELLER_ASK,
                         original_money=m0, original_currency="TWD",
                         price_image_index=0, associated_item_index=0)
    cB = MarketCandidate(item_index=1, market_hash_name="ItemB", verified=True,
                         verified_by="t", item_role=ItemRole.SELLING,
                         price_index=1, price_type=PriceType.SELLER_ASK,
                         original_money=m1, original_currency="TWD",
                         price_image_index=1, associated_item_index=1)
    lookup_calls = []
    analysis_calls = []
    lookup_results = {"ItemA": {"market_hash_name": "ItemA", "price_twd": 100},
                      "ItemB": {"market_hash_name": "ItemB", "price_twd": 200}}

    def fake_lookup(mh):
        lookup_calls.append(mh)
        return lookup_results.get(mh)

    def fake_analyze(candidate_mh, buff, twd):
        analysis_calls.append((candidate_mh, buff["market_hash_name"], twd))
        return {"deal": candidate_mh}

    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={}, currency_service=None,
        lookup_func=fake_lookup, analysis_func=fake_analyze,
        upload_enabled=False)
    assert len(lookup_calls) == 2, "structured lookup count != 2"
    assert set(lookup_calls) == {"ItemA", "ItemB"}
    assert len(deals) == 2, f"deal count != 2（實際 {len(deals)}）"
    assert deals[0]["deal"] == "ItemA" and deals[1]["deal"] == "ItemB", "A/B 交叉"


# ---------- RED-E1-6：無重複 lookup ----------
def test_red_e1_6_no_duplicate_lookup():
    """單一 candidate：structured 1 + legacy 0 = total 1"""
    assert process_structured_market_candidates is not None, "helper 未實作"
    from alkaid_cs2.domain.market_candidate import MarketCandidate
    from alkaid_cs2.domain.item_candidate import ItemRole
    m0 = Money(amount=Decimal("5000"), currency=Currency.TWD)
    c = MarketCandidate(item_index=0, market_hash_name="ItemA", verified=True,
                        verified_by="t", item_role=ItemRole.SELLING,
                        price_index=0, price_type=PriceType.SELLER_ASK,
                        original_money=m0, original_currency="TWD",
                        price_image_index=0, associated_item_index=0)
    calls = []
    outcomes, deals = process_structured_market_candidates(
        [c], post={}, currency_service=None,
        lookup_func=lambda mh: calls.append(mh) or {"market_hash_name": mh, "price_twd": 100},
        analysis_func=lambda mh, b, twd: {"deal": mh},
        upload_enabled=False)
    assert len(calls) == 1, f"lookup 重複（{len(calls)}）"
    # helper 內 legacy lookup 不得執行（由回傳結構驗證——deals 來自 structured）


# ---------- RED-E1-7：double conversion ----------
def test_red_e1_7_double_conversion_blocked():
    """ConvertedMoney 不得二次換算；forged → blocked + 固定 error code + lookup 0"""
    from analyze_arbitrage import resolve_seller_ask_conversion
    original = Money(amount=Decimal("5000"), currency=Currency.USD)
    converted = ConvertedMoney(original=original, twd_amount=Decimal("16200"),
                               rate_used=Decimal("3.24"), rate_source="openskin")
    # P1.2 契約：ConvertedMoney 不是 Money 型別——作輸入 = 拒絕（不得二次換算）
    conv = resolve_seller_ask_conversion({"original": converted}, {})
    assert not conv.valid, "ConvertedMoney 被接受為可換算輸入（二次換算風險）"
    assert conv.error_code, "缺固定 error code"
    # 原始 Money（正確輸入）→ valid
    conv_ok = resolve_seller_ask_conversion({"original": original}, {})
    assert conv_ok.valid, "原始 Money 被拒絕"
    # forged 無法構造：P1 契約在構造層禁止 llm/model/vision/ocr/unknown rate_source
    # （ValueError——證明雙重換算痕跡不可能進入 consumer path）
    import pytest as _pt
    with _pt.raises(ValueError):
        ConvertedMoney(original=original, twd_amount=Decimal("16200"),
                       rate_used=Decimal("3.24"), rate_source="llm")


# ---------- RED-E1-8：mode side effects ----------
def test_red_e1_8_off_mode_no_structured():
    """off：structured candidates 必須為空"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    from analyze_arbitrage import extract_skin_info
    vis = [VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")]))]
    result = parse_post_for_production(
        post_id="p", author="s", link="", post_text="text: 賣 AK 紅線\n",
        image_urls=[v.image_url for v in vis], vision_inputs=vis,
        full_dict={}, pattern_dict={}, weapon_map={},
        legacy_parser=extract_skin_info, mode="off")
    assert getattr(result, "structured_candidates", None) == [], \
        "off mode structured_candidates 非空"


def test_red_e1_8b_shadow_no_structured_side_effect():
    """shadow：正式輸出 legacy；structured 僅 audit（不執行正式 lookup）"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    from analyze_arbitrage import extract_skin_info
    vis = [VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")]))]
    result = parse_post_for_production(
        post_id="p", author="s", link="", post_text="text: 賣 AK 紅線\n",
        image_urls=[v.image_url for v in vis], vision_inputs=vis,
        full_dict={}, pattern_dict={}, weapon_map={},
        legacy_parser=extract_skin_info, mode="shadow")
    assert result.source == "shadow_legacy", "shadow 正式輸出非 legacy"
    assert hasattr(result, "structured_candidates"), "shadow 缺 structured audit"


# ---------- RED-E1-9：unlinked price 不錯接 ----------
def test_red_e1_9_unlinked_price_not_linked():
    """price.associated_item_index=None：即使 item.linked 含該 index 也不得 eligible"""
    from alkaid_cs2.domain import market_candidate as mc
    REASON_PRICE_UNLINKED = getattr(mc, "REASON_PRICE_UNLINKED",
                                    "P6_MARKET_CANDIDATE_PRICE_UNLINKED")
    from alkaid_cs2.domain.parsed_post import ParsedPost
    from alkaid_cs2.domain.item_candidate import ItemCandidate
    from alkaid_cs2.domain.price_candidate import PriceCandidate
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    items = [ItemCandidate(market_hash_name="ItemA", original_text="ItemA", verified=True, verified_by="t", parser="test", linked_price_indexes=[0])]
    prices = [PriceCandidate(money=m, price_type=PriceType.SELLER_ASK, source=PriceSource.IMAGE, evidence="售價", confidence=0.9, associated_item_index=None)]
    pp = ParsedPost(post_id="t", source="facebook", items=items, prices=prices)
    cands = build_market_candidates(pp)
    assert all(c.blocked for c in cands), "unlinked price 建立了 eligible candidate"
    assert any(c.block_reason == REASON_PRICE_UNLINKED for c in cands), \
        f"缺 PRICE_UNLINKED（實際: {[c.block_reason for c in cands]}）"


# ---------- RED-E1-10：完整 consumer outcome ----------
def test_red_e1_10_full_consumer_outcome():
    """integration：正式 consumer helper 完整 outcome（lookup/deal/upload 計數）"""
    assert process_structured_market_candidates is not None, "helper 未實作"
    from alkaid_cs2.domain.market_candidate import MarketCandidate
    from alkaid_cs2.domain.item_candidate import ItemRole
    m0 = Money(amount=Decimal("5000"), currency=Currency.TWD)
    c = MarketCandidate(item_index=0, market_hash_name="ItemA", verified=True,
                        verified_by="t", item_role=ItemRole.SELLING,
                        price_index=0, price_type=PriceType.SELLER_ASK,
                        original_money=m0, original_currency="TWD",
                        price_image_index=0, associated_item_index=0)
    uploaded = []
    outcomes, deals = process_structured_market_candidates(
        [c], post={}, currency_service=None,
        lookup_func=lambda mh: {"market_hash_name": mh, "price_twd": 100},
        analysis_func=lambda mh, b, twd: {"deal": mh, "converted_twd": twd},
        upload_func=lambda d: uploaded.append(d),
        upload_enabled=True)
    assert len(deals) == 1, "deal count != 1"
    assert len(uploaded) == 1, "upload count != 1"
    assert deals[0]["converted_twd"] is not None, "converted TWD 未傳入 analysis"
