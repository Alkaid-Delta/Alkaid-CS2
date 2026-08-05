# -*- coding: utf-8 -*-
"""test_structured_dispatch_independence.py — P6-R1-E4 authentic behavioral RED（6 個）

Structured Data Independence：structured dispatch 不得依賴 legacy result.data 存在。
"""
import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import pytest

from alkaid_cs2.domain.price_candidate import PriceType
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.market_candidate import MarketCandidate
from alkaid_cs2.domain.item_candidate import ItemRole
from alkaid_cs2.integration.production_bridge import ProductionParseResult
import analyze_arbitrage as aa
import alkaid_cs2.integration.production_bridge as pb


def _elig(mhn="ItemA", amt=5000):
    m = Money(amount=Decimal(str(amt)), currency=Currency.TWD)
    return [MarketCandidate(item_index=0, market_hash_name=mhn, verified=True,
                            verified_by="trusted_dictionary_exact", item_role=ItemRole.SELLING,
                            price_index=0, price_type=PriceType.SELLER_ASK,
                            original_money=m, original_currency="TWD",
                            price_image_index=0, associated_item_index=0)]


def _run(monkeypatch, mode, blocked, data, candidates):
    result = ProductionParseResult(data=data, source="v2", blocked=blocked,
                                   structured_candidates=candidates)
    stats = {"structured_inv": 0, "structured_lookup": 0, "legacy_lookup": 0}
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", mode)
    monkeypatch.setattr(pb, "parse_post_for_production", lambda *a, **k: result)
    orig_sc = aa.process_structured_market_candidates
    aa._in_trace = [False]

    def sc_wrap(*a, **k):
        stats["structured_inv"] += 1
        aa._in_trace[0] = True
        try:
            return orig_sc(*a, **k)
        finally:
            aa._in_trace[0] = False

    monkeypatch.setattr(aa, "process_structured_market_candidates", sc_wrap)
    monkeypatch.setattr(aa, "lookup_buff_price",
                        lambda mh: (stats.__setitem__("structured_lookup" if aa._in_trace[0] else "legacy_lookup",
                                                      stats["structured_lookup" if aa._in_trace[0] else "legacy_lookup"] + 1)
                                    or {"market_hash_name": mh, "price_twd": 100, "volume": 10}))
    monkeypatch.setattr(aa, "analyze_arbitrage",
                        lambda post, buff: {"deal": "x", "skin_name": "ItemA", "author": "A", "link": "http://x"})
    monkeypatch.setattr(aa, "upload_to_cloud", lambda d: None)
    monkeypatch.setattr(aa, "load_state", lambda: {})
    monkeypatch.setattr(aa, "mark_processed", lambda ids, st: None)
    monkeypatch.setattr(aa, "save_state", lambda st: None)
    monkeypatch.setattr(aa, "save_deal_to_history", lambda d: None)
    monkeypatch.setattr(aa, "print_deal_report", lambda d: None)
    post = {"id": "p", "author": "A", "url": "http://x", "content": "售 AK 5000", "images": []}
    try:
        aa.process_posts([post])
    except Exception as exc:  # RED：修正前可能 raise（data=None 未處理）
        stats["exception"] = repr(exc)
    return stats, post


# ---------- RED-E4-1：safe structured 不依賴 legacy data ----------
def test_red_e4_1_safe_structured_independent_of_data(monkeypatch):
    """data=None + eligible candidate：structured 執行（不得提前 skip）"""
    stats, post = _run(monkeypatch, "safe", False, None, _elig())
    assert stats["structured_inv"] == 1, f"structured 未執行（data=None 提前 skip——實際 {stats.get('exception', stats)}）"
    assert stats["structured_lookup"] == 1
    assert stats["legacy_lookup"] == 0


# ---------- RED-E4-2：v2_only structured 不依賴 legacy data ----------
def test_red_e4_2_v2_only_structured_independent_of_data(monkeypatch):
    stats, post = _run(monkeypatch, "v2_only", False, None, _elig())
    assert stats["structured_inv"] == 1, f"v2_only structured 未執行（實際 {stats.get('exception', stats)}）"
    assert stats["legacy_lookup"] == 0


# ---------- RED-E4-3：safe 無 structured 且 data=None ----------
def test_red_e4_3_safe_no_structured_data_none(monkeypatch):
    """safe + [] + data=None：fail-closed/skip——不 exception、legacy lookup=0"""
    stats, post = _run(monkeypatch, "safe", False, None, [])
    assert "exception" not in stats, f"exception: {stats.get('exception')}"
    assert stats["legacy_lookup"] == 0


# ---------- RED-E4-4：v2_only 無 structured 且 data=None ----------
def test_red_e4_4_v2_only_no_structured_data_none(monkeypatch):
    stats, post = _run(monkeypatch, "v2_only", False, None, [])
    assert "exception" not in stats, f"exception: {stats.get('exception')}"
    assert stats["structured_lookup"] == 0
    assert stats["legacy_lookup"] == 0


# ---------- RED-E4-5：blocked result 永遠 fail-closed ----------
def test_red_e4_5_blocked_fail_closed_even_with_candidates(monkeypatch):
    """blocked=True + eligible candidate：structured/legacy lookup 都 = 0"""
    stats, post = _run(monkeypatch, "safe", True, None, _elig())
    assert stats["structured_inv"] == 0, f"blocked 仍執行 structured（{stats}）"
    assert stats["legacy_lookup"] == 0


# ---------- RED-E4-6：legacy fallback 才要求 data ----------
def test_red_e4_6_legacy_fallback_requires_data(monkeypatch):
    """safe + [] + data 合法：legacy lookup=1；safe + [] + data=None：legacy lookup=0"""
    m = Money(amount=Decimal("5000"), currency=Currency.TWD)
    data_ok = {"market_hash_name": "AK-47 | Redline (Field-Tested)", "seller_price": 5000,
               "confidence": "high", "verified": True, "verified_by": "trusted_dictionary_exact",
               "validation_error": None, "original": m, "original_price": Decimal("5000"),
               "currency": "TWD"}
    stats1, _ = _run(monkeypatch, "safe", False, data_ok, [])
    assert stats1["legacy_lookup"] == 1, f"safe fallback 未執行（{stats1}）"
    stats2, _ = _run(monkeypatch, "safe", False, None, [])
    assert stats2["legacy_lookup"] == 0, f"data=None 仍 legacy lookup（{stats2}）"
