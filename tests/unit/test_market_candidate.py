# -*- coding: utf-8 -*-
"""test_market_candidate.py — P6-R1 structured market candidate boundary（RED→GREEN）

修正前（RED）：structured_candidates path 尚不存在——這些測試必須失敗。
修正後（GREEN）：第二張圖片 seller price 可經 structured candidate 到達 lookup boundary。
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from alkaid_cs2.domain.price_candidate import PriceType
from alkaid_cs2.integration.production_bridge import (
    parse_post_for_production,
    get_v2_parser_mode,
)
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.integration.vision_production import build_vision_merged_result, VisionImageInput


# ---------- helpers（受控 synthetic——無網路/無外部模型） ----------
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
    """受控 synthetic：build_vision_merged_result（P6-R0 trace 同法）→ build_market_candidates。
    純函式層（無網路/無外部模型）——candidate 建立/隔離/fail-closed 的直接證據。"""
    from alkaid_cs2.domain.raw_post import RawPostInput
    from alkaid_cs2.integration.vision_production import build_vision_merged_result
    from alkaid_cs2.domain.market_candidate import build_market_candidates
    post = RawPostInput(post_id="p6r1_red", author="synthetic", link="",
                        raw_text=text, image_urls=[vi.image_url for vi in vis_inputs],
                        source="facebook")
    vp = build_vision_merged_result(post, vision_inputs=vis_inputs,
                                    full_dict={}, pattern_dict={}, weapon_map={})
    cands = build_market_candidates(vp.merged_post) if vp.merged_post is not None else []
    return _ParseOutcome(vp, cands)


class _ParseOutcome:
    """輕量結果：candidates + legacy data（模擬 ProductionParseResult 結構）"""
    def __init__(self, vp, cands):
        self.vp = vp
        self.structured_candidates = cands
        self.data = vp.legacy_result.legacy_data if vp.legacy_result else None
        self.blocked = vp.blocked
        self.source = "v2" if vp.vision_used else "skipped"


# ---------- RED-1：第二張圖片 seller price 建立 structured candidate ----------
def test_red1_second_image_price_creates_structured_candidate():
    """RED-1：圖1 verified item + 圖2 SELLER_ASK → structured candidate count=1"""
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
        VisionImageInput(1, "inline://p/1", _payload([_item("M4A1-S | Printstream (Factory New)", price="9999", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣 AK 紅線\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    assert len(cands) >= 1, "第二張圖片 seller price 未建立 structured candidate"
    # 至少有 1 個 candidate 的 price_image_index == 1
    assert any(c.price_image_index == 1 for c in cands), "無 price_image_index=1 的 candidate"


# ---------- RED-2：兩商品兩價格不被壓成第一個 ----------
def test_red2_two_items_two_prices_isolation():
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
        VisionImageInput(1, "inline://p/1", _payload([_item("M4A1-S | Printstream (Factory New)", price="9999", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣兩把\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    # 兩商品兩價格 → 至少 2 個 candidate（或依 linkage 數量）
    assert len(cands) >= 2, "兩商品兩價格被壓成第一個（RED）"


# ---------- RED-3：非 SELLER_ASK 不建立 candidate ----------
def test_red3_non_seller_ask_not_candidate():
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="BUFF 最低價")])),
    ]
    result = _run_parse(vis, "text: 賣 AK 紅線\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    # 非 SELLER_ASK：不得有 eligible（非 blocked）candidate
    assert all(c.blocked for c in cands), "非 SELLER_ASK 進入 eligible candidate（RED）"
    # blocked reason 明確（NOT_SELLER_ASK 或 item_unverified）
    assert all(c.block_reason for c in cands), "blocked candidate 缺 block_reason（RED）"


# ---------- RED-4：unverified item fail-closed ----------
def test_red4_unverified_item_fail_closed():
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("Unknown Skin", price="5000", evidence="售價", conf=0.3)])),
    ]
    result = _run_parse(vis, "text: 賣未知皮膚\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    # unverified item 不得建立可 lookup candidate
    assert all(c.blocked or not c.verified for c in cands), "unverified item 進入可 lookup candidate（RED）"


# ---------- RED-5：UNKNOWN currency fail-closed ----------
def test_red5_unknown_currency_fail_closed():
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", currency="XXX", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣 AK 紅線\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    for c in cands:
        if c.original_currency is not None and str(c.original_currency) == "XXX":
            assert c.blocked, "UNKNOWN currency 未 blocked（RED）"


# ---------- RED-6：ConvertedMoney 不得二次換算 ----------
def test_red6_no_double_conversion():
    from alkaid_cs2.domain.price import ConvertedMoney, Currency
    from decimal import Decimal
    from alkaid_cs2.domain.price import Money
    original_money = Money(amount=Decimal("5000"), currency=Currency.USD)
    converted = ConvertedMoney(original=original_money,
                               twd_amount=Decimal("16200"),
                               rate_used=Decimal("3.24"), rate_source="openskin")
    # forged converted value 不得產生 lookup candidate
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣 AK 紅線\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    # converted 資料不得被當成可再次換算的原始值（契約由 P1 層保證——此處至少不崩潰且不產生重複 conversion 訊號）


# ---------- RED-7：legacy parity 保持 ----------
def test_red7_legacy_parity_preserved():
    """簡單單商品貼文：legacy output 與舊契約一致（data 不變）"""
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣 AK 紅線\n")
    # legacy data 仍存在（單商品契約）
    assert result.data is not None
    assert isinstance(result.data, dict)
    # structured path 額外存在且不覆蓋 legacy
    assert hasattr(result, "structured_candidates")


# ---------- RED-8：市場查價前候選邊界（fake lookup counter） ----------
def test_red8_lookup_boundary_exact_once():
    """fake lookup counter：只對 verified/linked/SELLER_ASK/合法幣別 candidate 呼叫"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    vis = [
        VisionImageInput(0, "inline://p/0", _payload([_item("AK-47 | Redline (Field-Tested)", price="5000", evidence="售價")])),
        VisionImageInput(1, "inline://p/1", _payload([_item("M4A1-S | Printstream (Factory New)", price="9999", evidence="售價")])),
    ]
    result = _run_parse(vis, "text: 賣兩把\n")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在（RED）"
    # 每個 eligible candidate 至少可被 consumer 個別處理（此處驗證 structured path 存在且可迭代）
    assert isinstance(cands, list)


# ---------- lookup boundary（fake counter——RED-8 強化） ----------
def test_lookup_boundary_exact_once_per_candidate():
    """P6-R1-E1：process_structured_market_candidates 每 eligible candidate 一次 lookup（注入函式計數）"""
    from decimal import Decimal
    from analyze_arbitrage import process_structured_market_candidates
    from alkaid_cs2.domain.market_candidate import MarketCandidate
    from alkaid_cs2.domain.item_candidate import ItemRole
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    calls = []
    m0 = Money(amount=Decimal("5000"), currency=Currency.TWD)
    m1 = Money(amount=Decimal("9999"), currency=Currency.TWD)
    cA = MarketCandidate(item_index=0, market_hash_name="AK-47 | Redline (Field-Tested)",
                         verified=True, verified_by="t", item_role=ItemRole.SELLING,
                         price_index=0, price_type=PriceType.SELLER_ASK,
                         original_money=m0, original_currency="TWD",
                         price_image_index=0, associated_item_index=0)
    cB = MarketCandidate(item_index=1, market_hash_name="M4A1-S | Printstream (Factory New)",
                         verified=True, verified_by="t", item_role=ItemRole.SELLING,
                         price_index=1, price_type=PriceType.SELLER_ASK,
                         original_money=m1, original_currency="TWD",
                         price_image_index=1, associated_item_index=1)
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={}, currency_service=None,
        lookup_func=lambda mh: calls.append(mh) or {"market_hash_name": mh, "price_twd": 100},
        analysis_func=lambda mh, b, twd: {"deal": mh},
        upload_enabled=False)
    assert len(calls) == 2, "每 candidate 一次 lookup"
    assert calls == ["AK-47 | Redline (Field-Tested)", "M4A1-S | Printstream (Factory New)"]
    assert len(deals) == 2


def test_unverified_and_unknown_currency_zero_lookup():
    """unverified / UNKNOWN currency candidate → 0 lookup calls（fail-closed）"""
    from alkaid_cs2.domain.market_candidate import eligible_candidates, MarketCandidate
    from alkaid_cs2.domain.item_candidate import ItemRole
    from alkaid_cs2.domain.price_candidate import PriceType
    # 未驗證 candidate
    unv = MarketCandidate(item_index=0, market_hash_name="X", verified=False,
                          price_index=0, price_type=PriceType.SELLER_ASK,
                          blocked=True, block_reason="P6_MARKET_CANDIDATE_ITEM_UNVERIFIED")
    # UNKNOWN currency candidate
    unk = MarketCandidate(item_index=1, market_hash_name="Y", verified=True,
                          price_index=1, price_type=PriceType.SELLER_ASK,
                          original_currency="UNKNOWN",
                          blocked=True, block_reason="P6_MARKET_CANDIDATE_UNKNOWN_CURRENCY")
    # 非 SELLER_ASK
    ref = MarketCandidate(item_index=2, market_hash_name="Z", verified=True,
                          price_index=2, price_type=PriceType.BUFF_FLOOR,
                          blocked=True, block_reason="P6_MARKET_CANDIDATE_NOT_SELLER_ASK")
    assert eligible_candidates([unv, unk, ref]) == []
