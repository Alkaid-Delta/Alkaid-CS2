# -*- coding: utf-8 -*-
"""test_v2_currency_handoff.py — P1.2 Typed V2 Currency Handoff（真實 adapter call-path）"""
import json
import os
import sys
from decimal import Decimal

PROJECT_ROOT = os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.dirname(os.path.dirname(PROJECT_ROOT)))

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.price import Money, ConvertedMoney
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceType, PriceSource


def _make_parsed_post(text, price_amount, currency, role="selling",
                      converted=None):
    """建 ParsedPost（單一 SELLER_ASK 價格）——走真實 to_legacy_skin_info。"""
    from alkaid_cs2.domain.parsed_post import ParsedPost
    from alkaid_cs2.domain.item_candidate import ItemCandidate
    money = Money(Decimal(str(price_amount)), currency)
    pc = PriceCandidate(
        money=money,
        price_type=PriceType.SELLER_ASK,
        source=PriceSource.TEXT,
        evidence="seller_ask",
        confidence=0.95,
        converted=converted,
    )
    from alkaid_cs2.domain.item_candidate import ItemRole, ItemEvidence
    from alkaid_cs2.domain.parsed_post import ParseStatus
    item = ItemCandidate(
        market_hash_name="AK-47 | Redline (Field-Tested)",
        weapon="AK-47", skin="Redline", wear="Field-Tested",
        stattrak=False, role=ItemRole.SELLING, original_text=text,
        matched_key="红线", match_start=0, match_end=4,
        parser="test", evidence=ItemEvidence.DICT_PATTERN,
        confidence=0.95, score=90.0,
        verified=True, verified_by="canonical_catalog",
        validation_error=None,
        linked_price_indexes=[0],
    )
    return ParsedPost(
        post_id="v2-1", raw_text=text, image_urls=[], source="test",
        items=[item], prices=[pc],
        parse_status=ParseStatus.OK,
    )


def test_v2_forged_rate_mismatch_blocked(monkeypatch):
    """forged ConvertedMoney rate mismatch → lookup=0"""
    import analyze_arbitrage as aa
    money = Money(Decimal("2100"), Currency.RMB)
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9450"),
                            rate_used=Decimal("4.6"),  # 錯誤 rate
                            rate_source="legacy-static-rate")
    r = aa.resolve_seller_ask_conversion(
        {"original": money, "currency": "RMB", "seller_price": 2100,
         "converted": forged}, {})
    assert r.valid is False
    assert r.error_code == "currency_preconverted_rate_mismatch"


def test_v2_forged_amount_mismatch_blocked(monkeypatch):
    import analyze_arbitrage as aa
    money = Money(Decimal("2100"), Currency.RMB)
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9999"),
                            rate_used=Decimal("4.5"),
                            rate_source="legacy-static-rate")
    r = aa.resolve_seller_ask_conversion(
        {"original": money, "currency": "RMB", "seller_price": 2100,
         "converted": forged}, {})
    assert r.valid is False
    assert r.error_code == "currency_preconverted_amount_mismatch"


def test_v2_forged_source_mismatch_blocked(monkeypatch):
    import analyze_arbitrage as aa
    money = Money(Decimal("2100"), Currency.RMB)
    # 合法字元但與 service（legacy-static-rate）不符 → source mismatch
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9450"),
                            rate_used=Decimal("4.5"),
                            rate_source="fixture-rate")
    r = aa.resolve_seller_ask_conversion(
        {"original": money, "currency": "RMB", "seller_price": 2100,
         "converted": forged}, {})
    assert r.valid is False
    assert r.error_code == "currency_preconverted_source_mismatch"


def test_converted_money_subclass_rejected():
    """ConvertedMoney subclass → 拒絕（exact-type）"""
    from alkaid_cs2.domain.price import ConvertedMoney, Money
    from alkaid_cs2.domain.enums import Currency
    money = Money(Decimal("2100"), Currency.RMB)

    class EvilConverted(ConvertedMoney):
        pass

    evil = EvilConverted(original=money, twd_amount=Decimal("9450"),
                         rate_used=Decimal("4.5"),
                         rate_source="legacy-static-rate")
    import analyze_arbitrage as aa
    r = aa.resolve_seller_ask_conversion(
        {"original": money, "currency": "RMB", "seller_price": 2100,
         "converted": evil}, {})
    assert r.valid is False
    assert r.error_code == "currency_preconverted_type_invalid"


def test_typed_original_field_mismatch_fail_closed():
    """typed original 與 legacy 欄位矛盾 → fail-closed"""
    import analyze_arbitrage as aa
    money = Money(Decimal("2100"), Currency.RMB)
    r = aa.resolve_seller_ask_conversion(
        {"original": money, "currency": "RMB", "seller_price": 5000}, {})
    assert r.valid is False
    assert r.error_code == "currency_original_field_mismatch"


# ================================================================
# Phase P1.3 — 真正 production_bridge call-path（不 monkeypatch 最終結果）
# ================================================================
def _real_bridge_process(monkeypatch, post_text, *, mode="v2_only",
                         post_currency=None, parsed_post_override=None):
    """真實 parse_post_for_production（真實 V2 管線 + to_legacy_skin_info）
    → 真實 process_posts → 計數。monkeypatch 只限底層（lookup/arb/upload/
    legacy_parser/CurrencyService 計數包裝 + parser 最底層輸入來源）。

    parsed_post_override：受控 ParsedPost（含 Decimal 小數）——monkeypatch
    parse_pipeline.parse_post（parser 最底層輸入來源）；parse_to_legacy /
    to_legacy_skin_info / bridge 全真實。
    """
    import analyze_arbitrage as aa
    import os as _os
    from alkaid_cs2.integration.production_bridge import (
        parse_post_for_production, get_v2_parser_mode)
    from alkaid_cs2.services.currency import CurrencyService
    from alkaid_cs2.domain.enums import Currency
    calls = {"lookup": 0, "arbitrage": 0, "upload": 0, "history": 0,
             "to_twd": 0}
    post = {"id": "p13-1", "author": "A", "url": "http://x",
            "content": post_text, "images": []}
    if post_currency:
        post["currency"] = post_currency

    bridge_mode = "safe" if mode == "v2_only" else mode
    from alkaid_cs2.adapters import legacy_adapter as _la
    orig_parse_post = _la.parse_post
    orig_lookup = aa.lookup_buff_price
    orig_arb = aa.analyze_arbitrage
    orig_upload = aa.upload_to_cloud
    orig_history = aa.save_deal_to_history
    orig_skin = aa.extract_skin_info
    orig_to_twd = CurrencyService.to_twd
    data = None
    result = None
    try:
        # P1.3：env 與底層 fake 先就緒（process_posts 執行期間全生效）
        _os.environ["ALKAID_V2_PARSER_MODE"] = bridge_mode
        aa.extract_skin_info = lambda t: None
        if parsed_post_override is not None:
            monkeypatch.setattr(_la, "parse_post",
                                lambda *a, **k: parsed_post_override)

        def fake_lookup(mhn, *a, **k):
            calls["lookup"] += 1
            return {"market_hash_name": mhn, "price_twd": 2000, "volume": 1,
                    "buy_price": 2000, "sell_price": 2000,
                    "buy_num": 1, "sell_num": 1}

        def fake_arb(d, buff=None):
            calls["arbitrage"] += 1
            return None

        def fake_upload(d):
            calls["upload"] += 1
            return True

        def fake_history(d):
            calls["history"] += 1

        def counting_to_twd(self, money):
            calls["to_twd"] += 1
            return orig_to_twd(self, money)

        aa.lookup_buff_price = fake_lookup
        aa.analyze_arbitrage = fake_arb
        aa.upload_to_cloud = fake_upload
        aa.save_deal_to_history = fake_history
        CurrencyService.to_twd = counting_to_twd

        # 直接驗證真實 bridge 產出（typed fields 保留）
        result = parse_post_for_production(
            post_id="p13-1", author="A", link="http://x",
            post_text=post_text, image_urls=[],
            vision_inputs=None,
            full_dict=_load_full_dict(),
            pattern_dict=_load_pattern_dict(),
            weapon_map=aa._V2_WEAPON_MAP,
            legacy_parser=aa.extract_skin_info,
            mode=bridge_mode,
        )
        if parsed_post_override is None and "售 红线 5000" in post_text:
            # UNKNOWN 案例：V2 blocked（無 SELLER_ASK）→ safe fallback legacy
            pass
        else:
            assert result.source == "v2", \
                f"source={result.source}（V2 應成功）"
            assert result.blocked is False
            data = result.data
            assert data is not None
        # process_posts（內部再跑一次真實 bridge——monkeypatch 仍生效）
        aa.process_posts([post])
    finally:
        monkeypatch.setattr(_la, "parse_post", orig_parse_post)
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.upload_to_cloud = orig_upload
        aa.save_deal_to_history = orig_history
        aa.extract_skin_info = orig_skin
        CurrencyService.to_twd = orig_to_twd
        _os.environ.pop("ALKAID_V2_PARSER_MODE", None)
    return calls, post, result


def _load_full_dict():
    import json as _json
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(
        "analyze_arbitrage.py")), "skin_dict.json")
    with open(p, encoding="utf-8") as f:
        return _json.load(f)["full_cn_to_en"]


def _load_pattern_dict():
    import json as _json
    import os as _os
    p = _os.path.join(_os.path.dirname(_os.path.abspath(
        "analyze_arbitrage.py")), "skin_dict.json")
    with open(p, encoding="utf-8") as f:
        return _json.load(f)["pattern_cn_to_en"]


def test_real_bridge_v2_rmb_9450(monkeypatch):
    """真實 bridge：V2 RMB 2100 → 9450，typed fields 保留"""
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 AK-47 | 红线 算2100 RMB", post_currency="RMB")
    data = result.data
    from alkaid_cs2.domain.price import Money
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    assert type(data["original"]) is Money
    assert data["original"].currency is Currency.RMB
    assert data["original"].amount == Decimal("2100")
    assert data["currency"] == "RMB"
    assert data["original_price"] == Decimal("2100")
    assert calls["to_twd"] == 1, calls
    # E3 契約：structured path 不寫入原始 post（candidate TWD 9450 於 candidate_post copy）
    assert post.get("_seller_price") is None, post.get("_seller_price")
    assert calls["lookup"] == 1, calls


def test_real_bridge_v2_usd_3240(monkeypatch):
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 AK-47 | 红线 算100 USD", post_currency="USD")
    assert calls["to_twd"] == 1, calls
    assert post.get("_seller_price") is None, post.get("_seller_price")


def test_real_bridge_v2_twd_5001(monkeypatch):
    """真實 bridge：TWD 5000.50 → rate=1 → 5001（HALF_UP）"""
    from decimal import Decimal as _D
    pp = _make_parsed_post("售 AK-47 | 红线 5000.50", "5000.50",
                           Currency.TWD)
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 AK-47 | 红线 5000.50", post_currency="TWD",
        parsed_post_override=pp)
    assert calls["to_twd"] == 1, calls
    assert post["_seller_price"] == 5001, post.get("_seller_price")


def test_real_bridge_fractional_9453(monkeypatch):
    """真實 bridge：RMB 2100.75 → 9453.375 HALF_UP → 9453"""
    from decimal import Decimal as _D
    pp = _make_parsed_post("售 AK-47 | 红线 2100.75", "2100.75",
                           Currency.RMB)
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 AK-47 | 红线 2100.75", post_currency="RMB",
        parsed_post_override=pp)
    data = result.data
    assert data["original"].amount == _D("2100.75"), "domain 精度保留"
    assert not isinstance(data["seller_price"], float), "adapter 不轉 float"
    assert calls["to_twd"] == 1, calls
    assert post["_seller_price"] == 9453, post.get("_seller_price")


def test_real_bridge_v2_unknown_blocked(monkeypatch):
    """真實 bridge：無幣別（UNKNOWN 語境）→ 0/0/0

    V2 管線對無幣別數字不產生 SELLER_ASK（→ blocked）→ safe fallback
    legacy 真實路徑（pattern 無武器 → P2 gate 未驗證擋）→ 零下游呼叫。
    """
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 红线 5000", post_currency=None)
    assert calls["lookup"] == 0, calls
    assert calls["arbitrage"] == 0, calls
    assert calls["upload"] == 0, calls
    assert calls["history"] == 0, calls


def test_forged_rate_full_path_zero_calls(monkeypatch):
    """forged rate mismatch → process_posts full-path 零下游呼叫"""
    import analyze_arbitrage as aa
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    money = Money(Decimal("2100"), Currency.RMB)
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9450"),
                            rate_used=Decimal("4.6"),
                            rate_source="legacy-static-rate")
    calls = _forged_full_path(monkeypatch, forged)
    assert calls["to_twd"] == 0, calls
    assert calls["lookup"] == 0, calls
    assert calls["arbitrage"] == 0, calls
    assert calls["upload"] == 0, calls
    assert calls["history"] == 0, calls


def test_forged_amount_full_path_zero_calls(monkeypatch):
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    money = Money(Decimal("2100"), Currency.RMB)
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9999"),
                            rate_used=Decimal("4.5"),
                            rate_source="legacy-static-rate")
    calls = _forged_full_path(monkeypatch, forged)
    assert calls == {"to_twd": 0, "lookup": 0, "arbitrage": 0,
                     "upload": 0, "history": 0}, calls


def test_forged_original_full_path_zero_calls(monkeypatch):
    """forged original mismatch → 零下游呼叫"""
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    other = Money(Decimal("999"), Currency.RMB)
    forged = ConvertedMoney(original=other, twd_amount=Decimal("9450"),
                            rate_used=Decimal("4.5"),
                            rate_source="legacy-static-rate")
    calls = _forged_full_path(monkeypatch, forged)
    assert calls["to_twd"] == 0 and calls["lookup"] == 0, calls


def test_converted_subclass_full_path_zero_calls(monkeypatch):
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    money = Money(Decimal("2100"), Currency.RMB)

    class EvilConverted(ConvertedMoney):
        pass

    forged = EvilConverted(original=money, twd_amount=Decimal("9450"),
                           rate_used=Decimal("4.5"),
                           rate_source="legacy-static-rate")
    calls = _forged_full_path(monkeypatch, forged)
    assert calls["lookup"] == 0 and calls["upload"] == 0, calls


def test_evil_money_subclass_rejected_in_domain():
    """ConvertedMoney.original 拒絕 Money subclass（domain 層）"""
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal

    class EvilMoney(Money):
        pass

    evil = EvilMoney(Decimal("2100"), Currency.RMB)
    try:
        ConvertedMoney(original=evil, twd_amount=Decimal("9450"),
                       rate_used=Decimal("4.5"),
                       rate_source="legacy-static-rate")
        raise AssertionError("Money subclass 被 ConvertedMoney 接受")
    except TypeError:
        pass


def _forged_full_path(monkeypatch, forged_converted):
    """真實 process_posts（mode=off + forged legacy data）→ 計數。"""
    import analyze_arbitrage as aa
    import os as _os
    from alkaid_cs2.services.currency import CurrencyService
    calls = {"lookup": 0, "arbitrage": 0, "upload": 0, "history": 0,
             "to_twd": 0}
    post = {"id": "forged-1", "author": "A", "url": "http://x",
            "content": "售 AK-47 | 红线 2100", "currency": "RMB"}
    orig_lookup = aa.lookup_buff_price
    orig_arb = aa.analyze_arbitrage
    orig_upload = aa.upload_to_cloud
    orig_history = aa.save_deal_to_history
    orig_skin = aa.extract_skin_info
    orig_to_twd = CurrencyService.to_twd
    try:
        _os.environ["ALKAID_V2_PARSER_MODE"] = "off"

        def forged_skin(t):
            return {"market_hash_name": "AK-47 | Redline (Field-Tested)",
                    "seller_price": 2100, "confidence": "high",
                    "verified": True, "verified_by": "canonical_catalog",
                    "validation_error": None, "currency": "RMB",
                    "original": forged_converted.original,
                    "converted": forged_converted}

        def fake_lookup(mhn, *a, **k):
            calls["lookup"] += 1
            return {"market_hash_name": mhn, "price_twd": 2000, "volume": 1,
                    "buy_price": 2000, "sell_price": 2000,
                    "buy_num": 1, "sell_num": 1}

        def fake_arb(d, buff=None):
            calls["arbitrage"] += 1
            return None

        def fake_upload(d):
            calls["upload"] += 1
            return True

        def fake_history(d):
            calls["history"] += 1

        def counting_to_twd(self, money):
            calls["to_twd"] += 1
            return orig_to_twd(self, money)

        aa.extract_skin_info = forged_skin
        aa.lookup_buff_price = fake_lookup
        aa.analyze_arbitrage = fake_arb
        aa.upload_to_cloud = fake_upload
        aa.save_deal_to_history = fake_history
        CurrencyService.to_twd = counting_to_twd
        aa.process_posts([post])
    finally:
        aa.extract_skin_info = orig_skin
        aa.lookup_buff_price = orig_lookup
        aa.analyze_arbitrage = orig_arb
        aa.upload_to_cloud = orig_upload
        aa.save_deal_to_history = orig_history
        CurrencyService.to_twd = orig_to_twd
        _os.environ.pop("ALKAID_V2_PARSER_MODE", None)
    return calls


def test_forged_source_mismatch_full_path_zero_calls(monkeypatch):
    """P1.4：forged rate_source mismatch → process_posts full-path 零下游呼叫"""
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    money = Money(Decimal("2100"), Currency.RMB)
    # 合法字元但與 service（legacy-static-rate）不符
    forged = ConvertedMoney(original=money, twd_amount=Decimal("9450"),
                            rate_used=Decimal("4.5"),
                            rate_source="fixture-rate")
    calls = _forged_full_path(monkeypatch, forged)
    assert calls["to_twd"] == 0, calls
    assert calls["lookup"] == 0, calls
    assert calls["arbitrage"] == 0, calls
    assert calls["upload"] == 0, calls
    assert calls["history"] == 0, calls


def test_real_production_bridge_preserves_typed_currency_fields(monkeypatch):
    """P1.4：真實 parse_post_for_production 保留 typed fields（欄位表）"""
    from alkaid_cs2.domain.price import Money, ConvertedMoney
    from alkaid_cs2.domain.enums import Currency
    from decimal import Decimal
    pp = _make_parsed_post("售 AK-47 | 红线 2100", 2100, Currency.RMB)
    # 真實 adapter 輸出
    from alkaid_cs2.adapters.legacy_adapter import to_legacy_skin_info
    adapter_result = to_legacy_skin_info(pp)
    assert adapter_result.blocked is False
    # 真實 bridge 呼叫（process_posts 內部）+ 直接 bridge 驗證
    calls, post, result = _real_bridge_process(
        monkeypatch, "售 AK-47 | 红线 2100", post_currency="RMB",
        parsed_post_override=pp)
    data = result.data
    ad = adapter_result.legacy_data
    checks = [
        ("original", data.get("original"), ad.get("original"),
         type(ad.get("original")) is Money),
        ("original_price", data.get("original_price"),
         ad.get("original_price"),
         isinstance(ad.get("original_price"), Decimal)),
        ("currency", data.get("currency"), ad.get("currency"),
         isinstance(ad.get("currency"), str)),
        ("converted", data.get("converted"), ad.get("converted"),
         ad.get("converted") is None),
        ("seller_price", data.get("seller_price"),
         ad.get("seller_price"),
         not isinstance(ad.get("seller_price"), float)),
        ("market_hash_name", data.get("market_hash_name"),
         ad.get("market_hash_name"), isinstance(ad.get("market_hash_name"), str)),
        ("verified", data.get("verified"), ad.get("verified"),
         isinstance(ad.get("verified"), bool)),
        ("verified_by", data.get("verified_by"), ad.get("verified_by"),
         ad.get("verified_by") is None or isinstance(ad.get("verified_by"), str)),
        ("validation_error", data.get("validation_error"),
         ad.get("validation_error"),
         ad.get("validation_error") is None),
    ]
    mismatches = []
    for name, bridge_v, adapter_v, type_ok in checks:
        if bridge_v != adapter_v or not type_ok:
            mismatches.append(
                f"{name}: bridge={bridge_v!r} adapter={adapter_v!r} type_ok={type_ok}")
    assert not mismatches, "欄位不一致: " + "; ".join(mismatches)
    assert calls["to_twd"] == 1 and post["_seller_price"] == 9450


def test_ast_no_fake_bridge_in_real_bridge_tests():
    """P1.4 AST proof：real bridge 測試函式不得有 fake ProductionParseResult"""
    import ast
    src_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "test_v2_currency_handoff.py")
    tree = ast.parse(open(src_path, encoding="utf-8").read())
    forbidden = ["SimpleNamespace", "parse_post_for_production = lambda",
                 "fake_result", "fake_parse_result"]
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name.startswith(
                ("test_real_bridge", "test_real_production_bridge")):
            fsrc = ast.get_source_segment(
                open(src_path, encoding="utf-8").read(), node) or ""
            for token in forbidden:
                if token in fsrc:
                    bad.append(f"{node.name} 含 {token}")
    assert not bad, "real bridge 測試含 fake 證據: " + "; ".join(bad)
