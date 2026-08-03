# -*- coding: utf-8 -*-
"""test_currency_hardening.py — Phase P1 Money and Currency Hardening（RED 先行）"""
import json
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.dirname(PROJECT_ROOT)))


def test_prompt_does_not_request_conversion():
    """LLM prompt 不得要求乘 4.5 換算（before：L680 有 → RED）"""
    import analyze_arbitrage as aa
    import inspect
    src = inspect.getsource(aa)
    assert "自動乘 4.5" not in src, "prompt 仍要求 LLM 乘 4.5 換算"
    assert "乘 4.5 轉成 TWD" not in src


def test_twd_not_reconverted_in_process_posts():
    """TWD 價格不得被再乘倍率（真實 process_posts call-path）"""
    import analyze_arbitrage as aa
    import os as _os
    calls = {"lookup": 0}
    orig_lookup = aa.lookup_buff_price
    orig_skin = aa.extract_skin_info
    try:
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"

        def fake_lookup(mhn, *a, **k):
            calls["lookup"] += 1
            return {"market_hash_name": mhn, "price_twd": 5000, "volume": 1,
                    "buy_price": 5000, "sell_price": 5000,
                    "buy_num": 1, "sell_num": 1}

        def fake_arb(data, buff=None):
            return None

        aa.lookup_buff_price = fake_lookup
        aa.analyze_arbitrage = fake_arb
        aa.extract_skin_info = lambda t: {
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "seller_price": 5000, "confidence": "high",
            "verified": True, "verified_by": "canonical_catalog",
            "validation_error": None, "currency": "TWD"}
        seen = {}
        orig_upload = aa.upload_to_cloud
        aa.upload_to_cloud = lambda d: (seen.__setitem__("sp", d.get("_seller_price", None)) or True)
        aa.process_posts([{"id": "t1", "content": "售 AK-47 | 红线 5000",
                           "currency": "TWD"}])
        # TWD 不得被乘（5000 保持 5000；若被 ×4.5 則 _seller_price=22500）
        assert seen.get("sp") in (5000, None), f"TWD 被再乘: {seen}"
        assert calls["lookup"] == 1
    finally:
        aa.lookup_buff_price = orig_lookup
        aa.extract_skin_info = orig_skin
        aa.upload_to_cloud = orig_upload
        _os.environ.pop("ALKAID_V2_PARSER_MODE", None)


def test_llm_returns_original_currency():
    """LLM 回傳必須含原始幣別（before：JSON schema 無 currency → RED）"""
    import analyze_arbitrage as aa
    import inspect
    src = inspect.getsource(aa)
    assert '"currency"' in src, "LLM JSON schema 未要求回傳原始幣別"


def test_currency_service_single_conversion():
    """RMB 只換算一次（USD 兩段合併一次）"""
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"),
                          usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    m = Money(Decimal("2100"), Currency.RMB)
    c = svc.to_twd(m)
    assert c.twd_amount == Decimal("9450")
    assert c.rate_used == Decimal("4.5")
    assert c.original is m
    assert isinstance(c, ConvertedMoney)
    assert not isinstance(c, Money), "ConvertedMoney 不得是 Money"


def test_converted_money_rejected_as_input():
    """ConvertedMoney 不得再傳入 to_twd（型別防線）"""
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"),
                          usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    m = Money(Decimal("2100"), Currency.RMB)
    c = svc.to_twd(m)
    try:
        svc.to_twd(c)
        raise AssertionError("ConvertedMoney 被接受為輸入")
    except TypeError:
        pass


def test_twd_not_converted():
    """TWD → rate=1 不變"""
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"),
                          usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    c = svc.to_twd(Money(Decimal("5000"), Currency.TWD))
    assert c.twd_amount == Decimal("5000")
    assert c.rate_used == Decimal("1")


def test_unknown_currency_rejected():
    """UNKNOWN 不得換算"""
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"),
                          usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    try:
        svc.to_twd(Money(Decimal("100"), Currency.UNKNOWN))
        raise AssertionError("UNKNOWN 被換算")
    except ValueError:
        pass


def test_float_rejected_in_money():
    """Money 不接受 float（核心型別）"""
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    try:
        Money(1.5, Currency.TWD)
        raise AssertionError("float 被接受")
    except TypeError:
        pass


def test_rate_source_present():
    """換算結果必須有 rate_source"""
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"),
                          usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    c = svc.to_twd(Money(Decimal("100"), Currency.RMB))
    assert c.rate_source == "legacy-static-rate"
    assert c.rate_source not in ("unknown", "llm", "model", "vision", "ocr")


def test_calculation_expression_not_double_converted():
    """2100*4.4=9200：9200 已是 TWD 不得再乘"""
    import analyze_arbitrage as aa
    import inspect
    src = inspect.getsource(aa)
    # before：process_posts L1156 對 legacy 結果（含 calculated 9200）再 ×4.5
    assert "post.get(\"currency\") == \"RMB\"" not in src or \
        "_convert_legacy_ask_to_twd" in src, \
        "換算仍在 process_posts 硬編碼（非單一邊界）"


# ================================================================
# P1 測試矩陣補齊（C pipeline / D prompt / E regression / F boundary）
# ================================================================
class _FakeMsg:
    def __init__(self, content):
        self.content = content


class _FakeCh:
    def __init__(self, content):
        self.message = _FakeMsg(content)


class _FakeR:
    def __init__(self, content):
        self.choices = [_FakeCh(content)]


class _FakeComp:
    def __init__(self, contents):
        self._it = iter(contents)

    def create(self, **kw):
        return _FakeR(json.dumps(next(self._it), ensure_ascii=False))


class _FakeChat2:
    def __init__(self, contents):
        self.completions = _FakeComp(contents)


class _FakeClient2:
    def __init__(self, contents):
        self.chat = _FakeChat2(contents)


def _legacy_extract_p1(post_text, *, verify_fn=None, client_contents=None):
    import analyze_arbitrage as aa
    orig_client = aa.create_client
    orig_verify = aa._verify_skin_on_csgoskins
    try:
        if client_contents is not None:
            aa.create_client = lambda: _FakeClient2(list(client_contents))
        if verify_fn is not None:
            aa._verify_skin_on_csgoskins = verify_fn
        return aa.extract_skin_info(post_text)
    finally:
        aa.create_client = orig_client
        aa._verify_skin_on_csgoskins = orig_verify


def test_pipeline_rmb_converts_exactly_once():
    """RMB seller ask 只轉一次（2100 × 4.5 = 9450，非 9450×4.5）"""
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 2100, "currency": "RMB"}, {})
    assert r.valid, r
    assert r.converted.twd_amount == 9450, r
    assert r.converted.rate_used == 4.5
    assert r.converted.rate_source == "legacy-static-rate"
    assert aa.quantize_twd_for_legacy_display(r.converted) == 9450


def test_pipeline_twd_not_converted():
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 5000, "currency": "TWD"}, {})
    assert r.valid
    assert r.converted.twd_amount == 5000
    assert r.converted.rate_used == 1
    assert aa.quantize_twd_for_legacy_display(r.converted) == 5000


def test_pipeline_usd_conversion():
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 100, "currency": "USD"}, {})
    assert r.valid
    assert aa.quantize_twd_for_legacy_display(r.converted) == int(100 * 7.2 * 4.5)


def test_pipeline_unknown_fail_closed():
    """UNKNOWN → fail-closed（price_currency_unresolved，不回原值）"""
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 3000, "currency": "UNKNOWN"}, {})
    assert r.valid is False
    assert r.converted is None
    assert r.error_code == "price_currency_unresolved"


def test_pipeline_currency_detection():
    import analyze_arbitrage as aa
    from alkaid_cs2.domain.enums import Currency
    assert aa._detect_currency({"currency": "RMB"}, {}) is Currency.RMB
    assert aa._detect_currency({"currency": "NT$"}, {}) is Currency.TWD
    assert aa._detect_currency({}, {"currency": "CNY"}) is Currency.RMB
    assert aa._detect_currency({"currency": "USD"}, {}) is Currency.USD
    assert aa._detect_currency({"currency": "UNKNOWN"}, {}) is Currency.UNKNOWN
    assert aa._detect_currency({}, {}) is Currency.UNKNOWN


def test_pipeline_llm_returns_original_rmb():
    """LLM 回傳 RMB 原始值 → 系統換算（非 LLM）"""
    import analyze_arbitrage as aa
    r = _legacy_extract_p1(
        "售 神秘商品Q 2100",
        verify_fn=lambda mhn: True,
        client_contents=[
            {"market_hash_name": "★ Sport Gloves | Nocts (Field-Tested)",
             "price": 2100, "currency": "RMB", "confidence": "high"}])
    assert r is not None
    assert r.get("seller_price") == 2100, "seller_price 應為原始金額"
    assert r.get("currency") == "RMB"


def test_legacy_v2_parity_same_input():
    """同一原始價格（RMB 2100）共用 stage 與 CurrencyService 結果一致"""
    import analyze_arbitrage as aa
    from alkaid_cs2.domain.enums import Currency
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.price import Money
    from decimal import Decimal
    svc = CurrencyService(rmb_to_twd=Decimal("4.5"), usd_to_rmb=Decimal("7.2"),
                          rate_source="legacy-static-rate")
    c = svc.to_twd(Money(Decimal("2100"), Currency.RMB))
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 2100, "currency": "RMB"}, {})
    assert r.valid
    assert r.converted.twd_amount == c.twd_amount
    assert r.converted.rate_used == c.rate_used


def test_calculation_expression_preserved():
    """2100*4.4=9200：9200 已是 TWD（currency=TWD）不再乘"""
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": 9200, "currency": "TWD"}, {})
    assert r.valid
    assert aa.quantize_twd_for_legacy_display(r.converted) == 9200
    assert r.converted.rate_used == 1


def test_validation_failure_not_converted():
    """validation failure（verified=False）不進換算"""
    import analyze_arbitrage as aa
    from alkaid_cs2.services.item_validator import require_verified_market_item
    unverified = {"market_hash_name": None, "verified": False,
                  "verified_by": None,
                  "validation_error": "item_validation_retry_failed",
                  "seller_price": 2100}
    assert require_verified_market_item(unverified) is None


def test_conversion_failure_blocks_arbitrage_upload():
    """conversion 失敗（UNKNOWN/無效）→ valid=False"""
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"seller_price": -1, "currency": "UNKNOWN"}, {})
    assert r.valid is False
    assert r.error_code == "price_invalid_amount" or r.error_code == \
        "price_currency_unresolved"
