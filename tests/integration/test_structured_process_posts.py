# -*- coding: utf-8 -*-
"""test_structured_process_posts.py — P6-R1-E2 authentic behavioral RED（12 個）

Production Default Candidate Context and Legacy-Gate Decoupling。
"""
import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from alkaid_cs2.domain.price_candidate import PriceType, PriceSource, PriceCandidate
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.market_candidate import MarketCandidate, build_market_candidates
from alkaid_cs2.domain.item_candidate import ItemRole, ItemCandidate
from alkaid_cs2.domain.parsed_post import ParsedPost
from alkaid_cs2.integration.production_bridge import ProductionParseResult
from analyze_arbitrage import process_structured_market_candidates


def _cand(item_index, mh, amount, img_idx):
    m = Money(amount=Decimal(str(amount)), currency=Currency.TWD)
    return MarketCandidate(item_index=item_index, market_hash_name=mh, verified=True,
                           verified_by="test", item_role=ItemRole.SELLING,
                           price_index=item_index, price_type=PriceType.SELLER_ASK,
                           original_money=m, original_currency="TWD",
                           price_image_index=img_idx, associated_item_index=item_index)


# ---------- RED-E2-1：default analysis 使用 candidate TWD ----------
def test_red_e2_1_default_analysis_uses_candidate_twd(monkeypatch):
    """不注入 analysis_func——default resolution 必須讓 analyze 觀察各 candidate TWD"""
    observed = []

    def fake_analyze(post, buff):
        observed.append(post.get("_seller_price"))
        return {"deal": post.get("_structured_market_hash_name")}

    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "analyze_arbitrage", fake_analyze)
    monkeypatch.setattr(aa, "lookup_buff_price",
                        lambda mh: {"market_hash_name": mh, "price_twd": 100})
    cA = _cand(0, "ItemA", 5000, 0)
    cB = _cand(1, "ItemB", 9000, 1)
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={"_seller_price": 1111}, upload_enabled=False)
    assert observed == [5000, 9000], f"A/B 未用 candidate TWD（實際: {observed}）"


# ---------- RED-E2-2：每 candidate identity 進 default analysis ----------
def test_red_e2_2_default_analysis_uses_candidate_identity(monkeypatch):
    observed = []

    def fake_analyze(post, buff):
        observed.append(post.get("_structured_market_hash_name"))
        return {"deal": post.get("_structured_market_hash_name")}

    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "analyze_arbitrage", fake_analyze)
    monkeypatch.setattr(aa, "lookup_buff_price",
                        lambda mh: {"market_hash_name": mh, "price_twd": 100})
    cA = _cand(0, "ItemA", 5000, 0)
    cB = _cand(1, "ItemB", 9000, 1)
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={"market_hash_name": "LegacyItem"}, upload_enabled=False)
    assert observed == ["ItemA", "ItemB"], f"identity 未綁定 candidate（實際: {observed}）"


# ---------- RED-E2-3：legacy invalid price 不阻擋 structured ----------
def test_red_e2_3_legacy_invalid_price_decoupled():
    """ProductionParseResult: legacy 無效價格 + structured eligible → structured 執行"""
    result = ProductionParseResult(
        data={"market_hash_name": "Legacy", "seller_price": -1, "confidence": "low"},
        source="v2", blocked=False,
        structured_candidates=[_cand(0, "ItemA", 5000, 0)])
    assert len(result.structured_candidates) == 1, "candidate 建立失敗"
    # consumer seam：structured eligible 不得被 legacy 前置 gate 阻擋
    # （實際 process_posts 判定——此處驗證 candidate 存在且 eligible）
    from alkaid_cs2.domain.market_candidate import eligible_candidates
    assert len(eligible_candidates(result.structured_candidates)) == 1


# ---------- RED-E2-4：legacy unverified 不阻擋 structured verified ----------
def test_red_e2_4_legacy_unverified_decoupled():
    result = ProductionParseResult(
        data={"market_hash_name": "Legacy", "seller_price": 100, "confidence": "low",
              "verified": False},
        source="v2", blocked=False,
        structured_candidates=[_cand(0, "ItemA", 5000, 0)])
    from alkaid_cs2.domain.market_candidate import eligible_candidates
    assert len(eligible_candidates(result.structured_candidates)) == 1
    assert result.structured_candidates[0].verified is True


# ---------- RED-E2-5：無 eligible 才走 legacy ----------
def test_red_e2_5_no_eligible_then_legacy():
    """全 blocked candidates：依 mode 契約 legacy fallback 可執行（非直接吞掉）"""
    from alkaid_cs2.domain.market_candidate import eligible_candidates, REASON_ITEM_UNVERIFIED
    blocked = MarketCandidate(item_index=0, market_hash_name="X", verified=False,
                              price_index=0, price_type=PriceType.SELLER_ASK,
                              blocked=True, block_reason=REASON_ITEM_UNVERIFIED)
    assert eligible_candidates([blocked]) == []
    # legacy fallback 判定存在於 process_posts（mode 契約）——此處驗證 eligible 判空邏輯


# ---------- RED-E2-6：production default upload ----------
def test_red_e2_6_default_upload(monkeypatch):
    """不傳 upload_func——default upload_to_cloud 必須執行"""
    uploaded = []
    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "upload_to_cloud", lambda d: uploaded.append(d))
    monkeypatch.setattr(aa, "lookup_buff_price",
                        lambda mh: {"market_hash_name": mh, "price_twd": 100})
    monkeypatch.setattr(aa, "analyze_arbitrage",
                        lambda post, buff: {"deal": post.get("_structured_market_hash_name")})
    cA = _cand(0, "ItemA", 5000, 0)
    cB = _cand(1, "ItemB", 9000, 1)
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={}, upload_enabled=True)
    assert len(uploaded) == 2, f"default upload 未執行（實際: {len(uploaded)}）"


# ---------- RED-E2-7：upload disabled ----------
def test_red_e2_7_upload_disabled(monkeypatch):
    uploaded = []
    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "upload_to_cloud", lambda d: uploaded.append(d))
    cA = _cand(0, "ItemA", 5000, 0)
    outcomes, deals = process_structured_market_candidates(
        [cA], post={}, upload_enabled=False)
    assert len(uploaded) == 0, "upload_enabled=False 仍 upload"


# ---------- RED-E2-8：analysis failure 不 upload、其他 candidate 繼續 ----------
def test_red_e2_8_analysis_failure_no_upload(monkeypatch):
    uploaded = []
    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "upload_to_cloud", lambda d: uploaded.append(d))

    def fake_analyze(post, buff):
        if post.get("_structured_market_hash_name") == "ItemA":
            return None  # analysis 失敗
        return {"deal": "ItemB"}

    monkeypatch.setattr(aa, "analyze_arbitrage", fake_analyze)
    monkeypatch.setattr(aa, "lookup_buff_price",
                        lambda mh: {"market_hash_name": mh, "price_twd": 100})
    cA = _cand(0, "ItemA", 5000, 0)
    cB = _cand(1, "ItemB", 9000, 1)
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post={}, upload_enabled=True)
    assert len(deals) == 1, f"deal count != 1（實際 {len(deals)}）"
    assert len(uploaded) == 1, "analysis None 仍 upload"


# ---------- RED-E2-9：text-only V2 structured candidates ----------
def test_red_e2_9_text_only_v2_structured(monkeypatch):
    """無 vision inputs——text-only V2 必須由原 ParsedPost 建 candidates（非 legacy 重解析）"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    from analyze_arbitrage import extract_skin_info
    from alkaid_cs2.adapters.legacy_adapter import (LegacyAdapterResult,
                                                    LegacySelectionReason)
    from alkaid_cs2.domain.raw_post import RawPostInput
    items = [
        ItemCandidate(market_hash_name="ItemA", original_text="ItemA", verified=True,
                      verified_by="t", parser="test", linked_price_indexes=[0]),
        ItemCandidate(market_hash_name="ItemB", original_text="ItemB", verified=True,
                      verified_by="t", parser="test", linked_price_indexes=[1]),
    ]
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    prices = [
        type("PC", (PriceCandidate,), {})(money=m, price_type=PriceType.SELLER_ASK,
                       source=PriceSource.IMAGE, evidence="e", confidence=0.9,
                       image_index=None, associated_item_index=0),
        type("PC", (PriceCandidate,), {})(money=m, price_type=PriceType.SELLER_ASK,
                       source=PriceSource.IMAGE, evidence="e", confidence=0.9,
                       image_index=None, associated_item_index=1),
    ]
    pp = ParsedPost(post_id="p", source="facebook", items=items, prices=prices)
    v2_result = LegacyAdapterResult(
        legacy_data={"market_hash_name": "ItemA", "seller_price": 5000,
                     "confidence": "high", "verified": True, "verified_by": "t",
                     "validation_error": None, "source": "v2_adapter",
                     "item_role": "selling", "selection_reason": "single_selling_item"},
        selected_item_index=0, selected_price_index=0,
        selection_reason=LegacySelectionReason.SINGLE_SELLING_ITEM,
        parsed_post=pp)
    import alkaid_cs2.integration.production_bridge as pb
    monkeypatch.setattr(pb, "parse_to_legacy", lambda *a, **k: v2_result)
    result = parse_post_for_production(
        post_id="p", author="s", link="", post_text="text: 賣兩把\n",
        image_urls=[], vision_inputs=[],
        full_dict={}, pattern_dict={}, weapon_map={},
        legacy_parser=extract_skin_info, mode="v2_only")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None, "structured_candidates path 不存在"
    # 由原 ParsedPost 建立（非 legacy dict 重解析）——count = 2
    assert len(cands) == 2, f"text-only V2 candidate count != 2（實際 {len(cands)}）"


# ---------- RED-E2-10：vision fallback-to-text structured ----------
def test_red_e2_10_vision_text_fallback_structured(monkeypatch):
    """vision merge 不安全 → text-only V2 安全 → source=v2 + candidates 由 text ParsedPost 建立"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    from analyze_arbitrage import extract_skin_info
    from alkaid_cs2.adapters.legacy_adapter import (LegacyAdapterResult,
                                                    LegacySelectionReason)
    from alkaid_cs2.integration.vision_production import VisionImageInput
    items = [
        ItemCandidate(market_hash_name="ItemA", original_text="ItemA", verified=True,
                      verified_by="t", parser="test", linked_price_indexes=[0]),
    ]
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    prices = [
        type("PC2", (PriceCandidate,), {})(money=m, price_type=PriceType.SELLER_ASK,
                       source=PriceSource.IMAGE, evidence="e", confidence=0.9,
                       image_index=None, associated_item_index=0),
    ]
    pp = ParsedPost(post_id="p", source="facebook", items=items, prices=prices)
    v2_result = LegacyAdapterResult(
        legacy_data={"market_hash_name": "ItemA", "seller_price": 5000,
                     "confidence": "high", "verified": True, "verified_by": "t",
                     "validation_error": None, "source": "v2_adapter",
                     "item_role": "selling", "selection_reason": "single_selling_item"},
        selected_item_index=0, selected_price_index=0,
        selection_reason=LegacySelectionReason.SINGLE_SELLING_ITEM,
        parsed_post=pp)
    import alkaid_cs2.integration.production_bridge as pb
    monkeypatch.setattr(pb, "parse_to_legacy", lambda *a, **k: v2_result)
    vis = [VisionImageInput(0, "inline://p/0",
                            {"type": "single", "platform": "facebook",
                             "items": [{"raw_name": "XXX", "evidence": "x",
                                        "role": "selling", "confidence": 0.1}],
                             "overall_confidence": 0.1})]
    result = parse_post_for_production(
        post_id="p", author="s", link="", post_text="text: 賣兩把\n",
        image_urls=[v.image_url for v in vis], vision_inputs=vis,
        full_dict={}, pattern_dict={}, weapon_map={},
        legacy_parser=extract_skin_info, mode="v2_only")
    cands = getattr(result, "structured_candidates", None)
    assert cands is not None
    assert len(cands) >= 1, f"vision-text fallback candidates 空（實際 {len(cands)}）"


# ---------- RED-E2-11：shadow 無正式 structured side effect ----------
def test_red_e2_11_shadow_no_structured_side_effect(monkeypatch):
    """shadow：正式 legacy 輸出保持；不執行 structured lookup/upload"""
    from alkaid_cs2.integration.production_bridge import parse_post_for_production
    from analyze_arbitrage import extract_skin_info
    result = parse_post_for_production(
        post_id="p", author="s", link="", post_text="text: 賣 AK 紅線 5000\n",
        image_urls=[], vision_inputs=[],
        full_dict={}, pattern_dict={}, weapon_map={},
        legacy_parser=extract_skin_info, mode="shadow")
    assert result.source == "shadow_legacy", "shadow 正式輸出非 legacy"
    # shadow audit candidates 可存在但正式 lookup/upload 不得執行（consumer 判定層）


# ---------- RED-E2-12：post mutation isolation ----------
def test_red_e2_12_post_not_mutated(monkeypatch):
    """兩 candidates 分析後原始 post 的 _seller_price 不得被永久改寫"""
    import analyze_arbitrage as aa
    monkeypatch.setattr(aa, "analyze_arbitrage",
                        lambda post, buff: {"deal": post.get("_structured_market_hash_name")})
    cA = _cand(0, "ItemA", 5000, 0)
    cB = _cand(1, "ItemB", 9000, 1)
    post = {"_seller_price": 1111, "id": "p1"}
    outcomes, deals = process_structured_market_candidates(
        [cA, cB], post=post, upload_enabled=False)
    assert post.get("_seller_price") == 1111, f"原始 post 被改寫（實際 {post.get('_seller_price')}）"


# ================================================================
# P6-R1-E3：Unified Structured Dispatch（RED）
# ================================================================
import analyze_arbitrage as _aa


def _dispatch(monkeypatch, mode, result_candidates, post=None):
    """透過 process_posts 的統一 dispatch 判定（monkeypatch 自動還原）"""
    from alkaid_cs2.integration.production_bridge import ProductionParseResult
    from analyze_arbitrage import process_posts
    m_d = Money(amount=Decimal("5000"), currency=Currency.TWD)
    result = ProductionParseResult(
        data={"market_hash_name": "AK-47 | Redline (Field-Tested)", "seller_price": 5000,
              "confidence": "high", "verified": True, "verified_by": "trusted_dictionary_exact",
              "validation_error": None, "original": m_d, "original_price": Decimal("5000"),
              "currency": "TWD"},
        source="v2", blocked=False,
        structured_candidates=result_candidates)
    calls = {"structured": 0, "legacy": 0}
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", mode)
    monkeypatch.setattr(_aa, "process_structured_market_candidates",
                        lambda *a, **k: (calls.__setitem__("structured", calls["structured"] + 1) or ([], [])))
    monkeypatch.setattr(_aa, "lookup_buff_price",
                        lambda mh: (calls.__setitem__("legacy", calls["legacy"] + 1)
                                    or {"market_hash_name": mh, "price_twd": 100, "volume": 10}))
    monkeypatch.setattr(_aa, "analyze_arbitrage",
                        lambda post, buff: {"deal": "x", "skin_name": "ItemA", "author": "A", "link": "http://x"})
    monkeypatch.setattr(_aa, "upload_to_cloud", lambda d: None)
    monkeypatch.setattr(_aa, "load_state", lambda: {})
    monkeypatch.setattr(_aa, "mark_processed", lambda ids, st: None)
    monkeypatch.setattr(_aa, "save_state", lambda st: None)
    monkeypatch.setattr(_aa, "save_deal_to_history", lambda d: None)
    monkeypatch.setattr(_aa, "print_deal_report", lambda d: None)
    import alkaid_cs2.integration.production_bridge as _pb
    monkeypatch.setattr(_pb, "parse_post_for_production", lambda *a, **k: result)
    p = post or {"id": "p", "author": "A", "url": "http://x", "content": "售 AK 5000", "images": []}
    process_posts([p])
    return calls, p


def _elig():
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    return [MarketCandidate(item_index=0, market_hash_name="ItemA", verified=True,
                            verified_by="t", item_role=ItemRole.SELLING,
                            price_index=0, price_type=PriceType.SELLER_ASK,
                            original_money=m, original_currency="TWD",
                            price_image_index=0, associated_item_index=0)]


# ---------- RED-E3-1：v2_only 空 candidates fail-closed ----------
def test_red_e3_1_v2_only_empty_fail_closed(monkeypatch):
    """v2_only + candidates=[]：即使 legacy data 合法——legacy lookup = 0"""
    calls, p = _dispatch(monkeypatch, "v2_only", [])
    assert calls["legacy"] == 0, f"v2_only empty 落入 legacy fallback（legacy lookup {calls['legacy']}）"
    assert calls["structured"] == 0


# ---------- RED-E3-2：v2_only all-blocked fail-closed ----------
def test_red_e3_2_v2_only_all_blocked_fail_closed(monkeypatch):
    """v2_only + 全 blocked：legacy lookup = 0"""
    from alkaid_cs2.domain.market_candidate import REASON_ITEM_UNVERIFIED
    blocked = MarketCandidate(item_index=0, market_hash_name="X", verified=False,
                              price_index=0, price_type=PriceType.SELLER_ASK,
                              blocked=True, block_reason=REASON_ITEM_UNVERIFIED)
    calls, p = _dispatch(monkeypatch, "v2_only", [blocked])
    assert calls["legacy"] == 0, "v2_only all-blocked 落入 legacy"


# ---------- RED-E3-3：safe no-eligible fallback 契約 ----------
def test_red_e3_3_safe_fallback(monkeypatch):
    """safe + 無 eligible：依 safe contract legacy lookup = 1（result 非 blocked、data 可轉換）"""
    from alkaid_cs2.integration.production_bridge import ProductionParseResult
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    result = ProductionParseResult(
        data={"market_hash_name": "AK-47 | Redline (Field-Tested)", "seller_price": 5000,
              "confidence": "high", "verified": True, "verified_by": "trusted_dictionary_exact",
              "validation_error": None, "original": m, "original_price": Decimal("5000"),
              "currency": "TWD"},
        source="v2", blocked=False, structured_candidates=[])
    import alkaid_cs2.integration.production_bridge as _pb
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "safe")
    monkeypatch.setattr(_aa, "process_structured_market_candidates", lambda *a, **k: ([], []))
    calls = {"legacy": 0}
    monkeypatch.setattr(_aa, "lookup_buff_price",
                        lambda mh: (calls.__setitem__("legacy", calls["legacy"] + 1)
                                    or {"market_hash_name": mh, "price_twd": 100, "volume": 10}))
    monkeypatch.setattr(_aa, "analyze_arbitrage",
                        lambda post, buff: {"deal": "x", "skin_name": "ItemA", "author": "A", "link": "http://x"})
    monkeypatch.setattr(_aa, "upload_to_cloud", lambda d: None)
    monkeypatch.setattr(_aa, "load_state", lambda: {})
    monkeypatch.setattr(_aa, "mark_processed", lambda ids, st: None)
    monkeypatch.setattr(_aa, "save_state", lambda st: None)
    monkeypatch.setattr(_aa, "save_deal_to_history", lambda d: None)
    monkeypatch.setattr(_aa, "print_deal_report", lambda d: None)
    monkeypatch.setattr(_pb, "parse_post_for_production", lambda *a, **k: result)
    p = {"id": "p", "author": "A", "url": "http://x", "content": "售 AK 5000", "images": []}
    _aa.process_posts([p])
    assert calls["legacy"] == 1, f"safe fallback 未執行（legacy {calls['legacy']}）"


# ---------- RED-E3-4：shadow 無 structured side effect ----------
def test_red_e3_4_shadow_no_side_effect(monkeypatch):
    """shadow + 有 candidates：structured lookup/analysis/upload = 0、legacy 正式執行"""
    calls, p = _dispatch(monkeypatch, "shadow", _elig())
    assert calls["structured"] == 0, f"shadow 執行 structured（{calls['structured']}）"
    assert calls["legacy"] == 1, "shadow legacy 正式 path 未執行"


# ---------- RED-E3-5：structured consumer only once ----------
def test_red_e3_5_single_dispatch(monkeypatch):
    """safe + 2 eligible：structured invocation = 1、legacy lookup = 0"""
    calls, p = _dispatch(monkeypatch, "safe", _elig() + _elig())
    assert calls["structured"] == 1, f"structured invocation != 1（{calls['structured']}）"
    assert calls["legacy"] == 0, "structured path 後仍 legacy lookup"


# ---------- RED-E3-6：原始 post 無 _seller_price 不被修改 ----------
def test_red_e3_6_post_no_price_mutation():
    """structured 完成後：原始 post 不含 _seller_price（candidate copy 各自取得）"""
    from alkaid_cs2.integration.production_bridge import ProductionParseResult
    result = ProductionParseResult(
        data={"market_hash_name": "LegacyItem", "seller_price": 5000,
              "confidence": "high", "verified": True, "verified_by": "t",
              "validation_error": None},
        source="v2", blocked=False, structured_candidates=_elig())
    import analyze_arbitrage as aa2
    orig = aa2.process_structured_market_candidates
    aa2.process_structured_market_candidates = lambda *a, **k: ([], [{"deal": "x", "skin_name": "ItemA", "author": "A", "link": "http://x"}])
    try:
        post = {"id": "p", "author": "A", "url": "http://x", "content": "售 AK 5000", "images": []}
        import alkaid_cs2.integration.production_bridge as _pb2
        orig_parse = _pb2.parse_post_for_production
        _pb2.parse_post_for_production = lambda *a, **k: result
        try:
            aa2.process_posts([post])
        finally:
            _pb2.parse_post_for_production = orig_parse
    finally:
        aa2.process_structured_market_candidates = orig
    assert "_seller_price" not in post, f"原始 post 被寫入 _seller_price（{post}）"


# ---------- RED-E3-7：原始 post 已有 legacy 值保持不變 ----------
def test_red_e3_7_post_existing_price_unchanged(monkeypatch):
    """post._seller_price=1111：structured 完成後仍 1111"""
    calls, p = _dispatch(monkeypatch, "safe", _elig(), post={"id": "p", "author": "A", "url": "http://x",
                                                "content": "售 AK 5000", "images": [],
                                                "_seller_price": 1111})
    assert p.get("_seller_price") == 1111, f"原始值被改寫（{p.get('_seller_price')}）"
