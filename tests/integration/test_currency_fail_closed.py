# -*- coding: utf-8 -*-
"""test_currency_fail_closed.py — P1.1 RED（真實 process_posts call-path）"""
import json
import os
import sys
from decimal import Decimal

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.dirname(PROJECT_ROOT)))


def _run_process_posts(monkeypatch, posts, mode="off", legacy_skin=None):
    """真實執行 process_posts，monkeypatch 計數 lookup/arbitrage/upload/to_twd。"""
    import analyze_arbitrage as aa
    import os as _os
    calls = {"lookup": 0, "arbitrage": 0, "upload": 0, "to_twd": 0}
    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", mode)
    orig_lookup = aa.lookup_buff_price
    orig_arb = aa.analyze_arbitrage
    orig_upload = aa.upload_to_cloud
    orig_skin = aa.extract_skin_info
    try:
        def fake_lookup(mhn, *a, **k):
            calls["lookup"] += 1
            return {"market_hash_name": mhn, "price_twd": 2000, "volume": 10,
                    "buy_price": 1000, "sell_price": 2000,
                    "buy_num": 10, "sell_num": 10}

        def fake_arb(data, buff=None):
            calls["arbitrage"] += 1
            return None  # 無套利 → 不 upload

        def fake_upload(data):
            calls["upload"] += 1
            return True

        aa.lookup_buff_price = fake_lookup
        aa.analyze_arbitrage = fake_arb
        aa.upload_to_cloud = fake_upload
        if legacy_skin is not None:
            aa.extract_skin_info = legacy_skin
        # CurrencyService.to_twd 計數
        from alkaid_cs2.services.currency import CurrencyService
        orig_to_twd = CurrencyService.to_twd
        def counting_to_twd(self, money):
            calls["to_twd"] += 1
            return orig_to_twd(self, money)
        CurrencyService.to_twd = counting_to_twd
        aa.process_posts(posts)
    finally:
        _os.environ.pop("ALKAID_V2_PARSER_MODE", None)
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.upload_to_cloud = orig_upload
        aa.extract_skin_info = orig_skin
        CurrencyService.to_twd = orig_to_twd
    return calls, posts[0]


def _legacy_info(**kw):
    base = {"market_hash_name": "AK-47 | Redline (Field-Tested)",
            "seller_price": 2100, "confidence": "high",
            "verified": True, "verified_by": "canonical_catalog",
            "validation_error": None, "currency": "RMB"}
    base.update(kw)
    return base


def test_unknown_currency_fail_closed_lookup_zero(monkeypatch):
    """mode=off + UNKNOWN currency → lookup=0 arbitrage=0 upload=0"""
    calls, post = _run_process_posts(
        monkeypatch,
        [{"id": "p1", "content": "售 AK-47 | 红线 2100", "currency": None}],
        legacy_skin=lambda t: _legacy_info(currency="UNKNOWN"))
    assert calls["lookup"] == 0, calls
    assert calls["arbitrage"] == 0, calls
    assert calls["upload"] == 0, calls


def test_unknown_currency_not_defaulted_to_twd(monkeypatch):
    """UNKNOWN 不得被視為 TWD 進套利（post 標記 RMB 也一樣）"""
    import analyze_arbitrage as aa
    calls, post = _run_process_posts(
        monkeypatch,
        [{"id": "p1", "content": "售 AK-47 | 红线 2100", "currency": "RMB"}],
        legacy_skin=lambda t: _legacy_info(currency="UNKNOWN"))
    assert calls["lookup"] == 0, "UNKNOWN 仍進 lookup"


def test_legacy_rmb_single_conversion_9450(monkeypatch):
    """legacy RMB 2100 → to_twd=1 → 最終 _seller_price=9450 → lookup=1"""
    import analyze_arbitrage as aa
    seen = {}
    orig_skin = aa.extract_skin_info
    orig_upload = aa.upload_to_cloud
    def skin(t):
        return _legacy_info()
    def spy_upload(d):
        seen["sp"] = d.get("_seller_price", None)
        return True
    aa.upload_to_cloud = spy_upload
    try:
        calls, post = _run_process_posts(
            monkeypatch,
            [{"id": "p1", "content": "售 AK-47 | 红线 2100 RMB",
              "currency": "RMB"}],
            legacy_skin=skin)
    finally:
        aa.upload_to_cloud = orig_upload
    assert calls["to_twd"] == 1, f"RMB 應只換算一次: {calls}"
    assert calls["lookup"] == 1, calls
    # 最終 _seller_price 精確斷言（post 快照）
    assert post["_seller_price"] == 9450, f"_seller_price={post.get('_seller_price')}"


def test_legacy_twd_unchanged(monkeypatch):
    """legacy TWD 5000 → rate=1 不變 → 5000"""
    import analyze_arbitrage as aa
    calls, post = _run_process_posts(
        monkeypatch,
        [{"id": "p1", "content": "售 AK-47 | 红线 5000", "currency": "TWD"}],
        legacy_skin=lambda t: _legacy_info(seller_price=5000, currency="TWD"))
    assert calls["to_twd"] == 1
    assert calls["lookup"] == 1, calls
    assert post["_seller_price"] == 5000


def test_decimal_precision_preserved():
    """parse_original_amount 保留 Decimal 精度（2100.75 / 2100.50）"""
    import analyze_arbitrage as aa
    if not hasattr(aa, "parse_original_amount"):
        raise AssertionError("parse_original_amount 不存在")
    assert aa.parse_original_amount(Decimal("2100.75")) == Decimal("2100.75")
    assert aa.parse_original_amount("2100.50") == Decimal("2100.50")
    assert aa.parse_original_amount(2100) == Decimal("2100")


def test_float_rejected_in_parse():
    import analyze_arbitrage as aa
    if not hasattr(aa, "parse_original_amount"):
        raise AssertionError("parse_original_amount 不存在")
    try:
        aa.parse_original_amount(2100.5)
        raise AssertionError("float 被接受")
    except TypeError:
        pass
    try:
        aa.parse_original_amount(True)
        raise AssertionError("bool 被接受")
    except TypeError:
        pass


def test_money_no_int_truncation():
    """Money 建構不經 int(sp)（Decimal 精度保留）"""
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    m = Money(Decimal("2100.75"), Currency.RMB)
    assert m.amount == Decimal("2100.75")


def test_converted_money_validation():
    """ConvertedMoney 契約：rate_used>0、rate_source 合法"""
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    m = Money(Decimal("100"), Currency.RMB)
    c = ConvertedMoney(original=m, twd_amount=Decimal("450"),
                       rate_used=Decimal("4.5"), rate_source="legacy-static-rate")
    assert c.twd_amount == Decimal("450")
    # 非法 rate_source
    try:
        ConvertedMoney(original=m, twd_amount=Decimal("450"),
                       rate_used=Decimal("4.5"), rate_source="llm")
        raise AssertionError("rate_source=llm 被接受")
    except ValueError:
        pass
    # 非正 rate
    try:
        ConvertedMoney(original=m, twd_amount=Decimal("450"),
                       rate_used=Decimal("0"), rate_source="legacy-static-rate")
        raise AssertionError("rate_used=0 被接受")
    except ValueError:
        pass


def test_retry_prompt_no_conversion_contract():
    """retry prompt 不得要求換算；應回傳原始 amount/currency"""
    import analyze_arbitrage as aa
    import inspect
    src = inspect.getsource(aa)
    assert "乘 4.5" not in src.split("retry_prompt")[1][:2000] or \
        "retry_prompt" not in src or "4.5" not in src.split("retry_prompt")[1][:1500], \
        "retry prompt 含換算指示"


def test_v2_rmb_single_conversion():
    """V2 source RMB 2100 → 共用 stage 只換算一次 → 9450"""
    import analyze_arbitrage as aa
    from alkaid_cs2.domain.enums import Currency
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from decimal import Decimal
    # V2 只有 original Money（無 converted）→ 共用 stage 換算一次
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 2100, "currency": "RMB"}, {})
    assert r.valid
    assert aa.quantize_twd_for_legacy_display(r.converted) == 9450
    # V2 已有合法 ConvertedMoney → 不得再換算（直接使用）
    m = Money(Decimal("2100"), Currency.RMB)
    c = ConvertedMoney(original=m, twd_amount=Decimal("9450"),
                       rate_used=Decimal("4.5"),
                       rate_source="legacy-static-rate")
    r2 = aa.resolve_seller_ask_conversion(
        {"seller_price": 2100, "currency": "RMB", "converted": c}, {})
    assert r2.valid
    assert r2.converted is c  # 同一物件，不再乘
    # V2 裸數字無 currency → fail-closed
    r3 = aa.resolve_seller_ask_conversion({"seller_price": 2100}, {})
    assert r3.valid is False
    assert r3.error_code == "price_currency_unresolved"
