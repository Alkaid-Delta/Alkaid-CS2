"""
test_production_bridge.py — 受控 production 橋接測試（Phase 6.2）

驗證：mode 行為（off/shadow/safe/v2_only）、seller_price 防守、metrics、
不重複換算、輸入不污染。
"""
import sys
import os
import math
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus  # noqa: E402
from alkaid_cs2.domain.price import Money  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType  # noqa: E402
from alkaid_cs2.domain.raw_post import RawPostInput  # noqa: E402
from alkaid_cs2.adapters.legacy_adapter import (  # noqa: E402
    LegacyAdapterResult,
    LegacySelectionReason,
)
from alkaid_cs2.integration.production_bridge import (  # noqa: E402
    ProductionParseMetrics,
    ProductionParseResult,
    get_v2_parser_mode,
    is_valid_legacy_seller_price,
    parse_post_for_production,
)

FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {"红线": "Redline", "紅線": "Redline", "火神": "Vulcan", "夜行衣": "Nocts"}
WEAPON_MAP = {"AK-47": "AK-47", "ak": "AK-47"}

# legacy parser stub：依文字回傳固定結果
LEGACY_OK = {"market_hash_name": "AK-47 | Redline (Field-Tested)", "seller_price": 5000, "confidence": "high"}
LEGACY_NONE = None


def legacy_parser(text):
    if "無" in text and "皮膚" in text:
        return None
    return dict(LEGACY_OK)


def make_parsed_post(items, prices, status=ParseStatus.OK, intent=ItemRole.SELLING):
    return ParsedPost(post_id="p1", raw_text="x", items=items, prices=prices,
                      parse_status=status, intent=intent, source="test")


def make_item(skin="Redline", role=ItemRole.SELLING):
    return ItemCandidate(
        market_hash_name=f"AK-47 | {skin} (Field-Tested)", skin=skin, weapon="AK-47",
        wear="Field-Tested", role=role, original_text="售 AK-47 | 红线 久经沙场 5000",
        matched_key=skin, match_start=2, match_end=12, parser="item_parser",
        evidence=ItemEvidence.DICT_FULL, confidence=0.95, score=100.0,
    )


def make_price(amount="5000", ptype=PriceType.SELLER_ASK, currency=Currency.TWD):
    return PriceCandidate(
        money=Money(Decimal(amount), currency), price_type=ptype,
        source=PriceSource.TEXT, evidence="5000", confidence=0.9,
        text_start=0, text_end=4,
    )


def bridge(post_text="售 紅線 算5000", mode="safe", legacy=legacy_parser):
    return parse_post_for_production(
        post_id="p1", author="A", link="http://x", post_text=post_text,
        image_urls=[], full_dict=FULL_DICT, pattern_dict=PATTERN_DICT,
        weapon_map=WEAPON_MAP, legacy_parser=legacy, mode=mode,
    )


# ================================================================
# 1-2. off：只 legacy；shadow：legacy 照常
# ================================================================
def test_off_uses_legacy_only(monkeypatch):
    calls = []

    def spy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    monkeypatch.delenv("ALKAID_V2_PARSER_MODE", raising=False)
    r = bridge(post_text="售 紅線 算5000", mode="off", legacy=spy)
    assert r.source == "legacy"
    assert r.data == LEGACY_OK
    assert calls == ["售 紅線 算5000"]  # V2 未執行（只有 legacy 一次呼叫）
    assert r.shadow_diff is None


def test_shadow_returns_legacy():
    r = bridge(post_text="售 紅線 算5000", mode="shadow")
    assert r.source == "shadow_legacy"
    assert r.data == LEGACY_OK  # legacy 是正式輸出
    assert r.shadow_diff is not None


# ================================================================
# 3-4. shadow 記錄差異
# ================================================================
def test_shadow_records_name_diff():
    r = bridge(post_text="售 紅線 算5000", mode="shadow")
    assert "legacy_market_hash_name" in r.shadow_diff
    assert "v2_market_hash_name" in r.shadow_diff
    assert "name_match" in r.shadow_diff


def test_shadow_records_price_diff():
    r = bridge(post_text="售 紅線 算5000", mode="shadow")
    assert "legacy_seller_price" in r.shadow_diff
    assert "v2_seller_price" in r.shadow_diff
    assert "price_match" in r.shadow_diff


# ================================================================
# 5-8. safe 模式
# ================================================================
def test_safe_uses_v2_for_single_twd():
    r = bridge(post_text="售 紅線 算5000", mode="safe")
    assert r.source == "v2"
    assert r.data is not None
    assert r.data["seller_price"] == 5000


def test_safe_falls_back_for_multi_item():
    r = bridge(post_text="紅線 火神 14000 7480", mode="safe")
    # V2 blocked（多商品多 ask）→ fallback legacy
    assert r.source == "legacy"
    assert r.data == LEGACY_OK
    assert any(w.startswith("v2_fallback") for w in r.warnings)


def test_safe_falls_back_for_no_price():
    r = bridge(post_text="售 紅線", mode="safe")
    # V2 無 SELLER_ASK → seller_price=None → fallback legacy
    assert r.source == "legacy"
    assert any(w.startswith("v2_fallback") for w in r.warnings)


def test_safe_falls_back_for_rmb():
    """P1.2：V2 adapter 不再 block RMB——透傳原始（由 stage 換算）"""
    r = bridge(post_text="售 紅線 9500RMB", mode="safe")
    assert r.source == "v2"
    assert r.data["currency"] == "RMB"
    assert r.data["original_price"] == 9500
    assert r.data["converted"] is None


# ================================================================
# 9-10. v2_only
# ================================================================
def test_v2_only_uses_v2():
    r = bridge(post_text="售 紅線 算5000", mode="v2_only")
    assert r.source == "v2"
    assert r.data["seller_price"] == 5000


def test_v2_only_skips_ambiguous():
    r = bridge(post_text="紅線 火神 14000 7480", mode="v2_only")
    assert r.source == "skipped"
    assert r.blocked is True
    assert r.data is None
    assert any(w.startswith("v2_blocked") for w in r.warnings)


# ================================================================
# 11. invalid mode → off
# ================================================================
def test_invalid_mode_defaults_off(monkeypatch):
    spy_calls = []

    def spy(text):
        spy_calls.append(text)
        return dict(LEGACY_OK)

    monkeypatch.setenv("ALKAID_V2_PARSER_MODE", "bogus")
    assert get_v2_parser_mode() == "off"
    r = bridge(post_text="售 紅線 算5000", mode="bogus", legacy=spy)
    assert r.source == "legacy"
    assert spy_calls  # legacy 被呼叫


# ================================================================
# 12-13. legacy 呼叫時機
# ================================================================
def test_legacy_parser_not_called_when_v2_used():
    calls = []

    def spy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    r = bridge(post_text="售 紅線 算5000", mode="safe", legacy=spy)
    assert r.source == "v2"
    assert calls == [], f"V2 成功時不應呼叫 legacy: {calls}"


def test_legacy_parser_called_on_safe_fallback():
    calls = []

    def spy(text):
        calls.append(text)
        return dict(LEGACY_OK)

    r = bridge(post_text="紅線 火神 14000 7480", mode="safe", legacy=spy)
    assert r.source == "legacy"
    assert len(calls) == 1


# ================================================================
# 14. V2 結果不再 ×4.5（bridge 層不換算；process_posts 層由 integration 測試驗證）
# ================================================================
def test_no_double_conversion_for_v2():
    r = bridge(post_text="售 紅線 算5000", mode="safe")
    assert r.source == "v2"
    # adapter 保證 TWD 5000，bridge 不得乘任何倍率
    assert r.data["seller_price"] == 5000


# ================================================================
# 15-20. is_valid_legacy_seller_price
# ================================================================
def test_valid_seller_price_rejects_none():
    assert is_valid_legacy_seller_price(None) is False


def test_valid_seller_price_rejects_bool():
    assert is_valid_legacy_seller_price(True) is False
    assert is_valid_legacy_seller_price(False) is False


def test_valid_seller_price_rejects_nan():
    assert is_valid_legacy_seller_price(float("nan")) is False


def test_valid_seller_price_rejects_inf():
    assert is_valid_legacy_seller_price(float("inf")) is False
    assert is_valid_legacy_seller_price(float("-inf")) is False


def test_valid_seller_price_rejects_zero():
    assert is_valid_legacy_seller_price(0) is False
    assert is_valid_legacy_seller_price(-1) is False


def test_valid_seller_price_accepts_int_decimal_rejects_float_bool():
    """P1.3：int/Decimal 接受；float 拒絕（adapter 不產生 float）"""
    from decimal import Decimal
    assert is_valid_legacy_seller_price(123) is True
    assert is_valid_legacy_seller_price(Decimal("123.45")) is True
    assert is_valid_legacy_seller_price(123.45) is False  # float 拒收
    assert is_valid_legacy_seller_price(0) is False
    assert is_valid_legacy_seller_price(True) is False

def test_metrics_increment_correctly():
    m = ProductionParseMetrics()
    r1 = bridge(post_text="售 紅線 算5000", mode="safe")   # v2
    r2 = bridge(post_text="紅線 火神 14000 7480", mode="safe")  # legacy fallback
    r3 = bridge(post_text="紅線 火神 14000 7480", mode="v2_only")  # skipped blocked
    m.record(r1)
    m.record(r2)
    m.record(r3)
    assert m.total == 3
    assert m.v2_used == 1
    assert m.legacy_used == 1
    assert m.skipped == 1
    assert m.v2_blocked == 1
    assert m.v2_fallback == 1


# ================================================================
# 22. warnings unique
# ================================================================
def test_warnings_unique():
    r = bridge(post_text="紅線 火神 14000 7480", mode="safe")
    assert len(r.warnings) == len(set(r.warnings))


# ================================================================
# 23. 無外部呼叫
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    r = bridge(post_text="售 紅線 算5000", mode="safe")
    assert r.source == "v2"


# ================================================================
# 24. 輸入不被污染
# ================================================================
def test_input_not_mutated():
    urls = ["https://x/1.jpg"]
    r = parse_post_for_production(
        post_id="p1", author="A", link="http://x", post_text="售 紅線 算5000",
        image_urls=urls, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT,
        weapon_map=WEAPON_MAP, legacy_parser=legacy_parser, mode="safe",
    )
    assert urls == ["https://x/1.jpg"]  # 未被修改
    assert r.source == "v2"


# ================================================================
# 25. ProductionParseResult 驗證
# ================================================================
def test_production_result_validation():
    # 非法 source
    with pytest.raises(ValueError):
        ProductionParseResult(data=None, source="bogus", blocked=False)
    # blocked=True + data 非 None
    with pytest.raises(ValueError):
        ProductionParseResult(data={"x": 1}, source="skipped", blocked=True)
    # warnings 含空白
    with pytest.raises(ValueError):
        ProductionParseResult(data=None, source="skipped", blocked=True, warnings=["  "])
    # 正常
    r = ProductionParseResult(data=None, source="skipped", blocked=True)
    assert r.source == "skipped"
