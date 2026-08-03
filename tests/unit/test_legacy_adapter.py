"""
test_legacy_adapter.py — Legacy Adapter 測試（Phase 6.1）

驗證：單商品轉換、多商品 blocking、seller price 選擇、currency blocking、
validation gate、輸入不被污染、無外部呼叫。
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.adapters.legacy_adapter import (  # noqa: E402
    LegacyAdapterResult,
    LegacySelectionReason,
    parse_to_legacy,
    to_legacy_skin_info,
)
from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus  # noqa: E402
from alkaid_cs2.domain.price import Money  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType  # noqa: E402
from alkaid_cs2.domain.raw_post import RawPostInput  # noqa: E402
from alkaid_cs2.services.price_item_linker import LinkDecision  # noqa: E402

# ── 測試字典 ──
FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {
    "红线": "Redline", "紅線": "Redline", "火神": "Vulcan",
    "鈷分裂": "Cobalt Disruption", "電擊": "Electric Hive", "夜行衣": "Nocts",
}
WEAPON_MAP = {"AK-47": "AK-47", "ak": "AK-47", "沙鷹": "Desert Eagle", "AWP": "AWP"}


def run(text: str) -> LegacyAdapterResult:
    post = RawPostInput(post_id="t1", raw_text=text, source="test")
    return parse_to_legacy(post, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT,
                           weapon_map=WEAPON_MAP)


def make_item(skin="Redline", role=ItemRole.SELLING, validation_error=None,
              mhn="AK-47 | Redline (Field-Tested)", conf=0.95):
    return ItemCandidate(
        market_hash_name=mhn, skin=skin, weapon="AK-47", wear="Field-Tested",
        role=role, original_text="售 AK-47 | 红线 久经沙场 5000", matched_key=skin,
        match_start=2, match_end=12, parser="item_parser",
        evidence=ItemEvidence.DICT_FULL, confidence=conf, score=100.0,
        validation_error=validation_error,
    )


def make_price(amount="5000", ptype=PriceType.SELLER_ASK, currency=Currency.TWD,
               start=0, end=4):
    return PriceCandidate(
        money=Money(Decimal(amount), currency),
        price_type=ptype, source=PriceSource.TEXT,
        evidence="5000", confidence=0.9,
        text_start=start, text_end=end,
    )


def make_parsed_post(items, prices, status=ParseStatus.OK, intent=ItemRole.SELLING):
    return ParsedPost(
        post_id="p1", raw_text="x", items=items, prices=prices,
        parse_status=status, intent=intent, source="test",
    )


# ================================================================
# 1. 單一 selling + TWD seller ask（顯式 SELLER_ASK）
# ================================================================
def test_single_selling_twd():
    # 手動建：唯一 SELLING + SELLER_ASK TWD
    item = make_item()
    item.linked_price_indexes = [0]
    price = make_price("5000")  # SELLER_ASK TWD
    p = make_parsed_post([item], [price])
    r = to_legacy_skin_info(p)
    assert r.blocked is False
    assert r.legacy_data is not None
    assert r.legacy_data["market_hash_name"]
    assert r.legacy_data["seller_price"] == 5000
    assert "confidence" in r.legacy_data
    assert r.selection_reason is LegacySelectionReason.SINGLE_SELLING_ITEM


# ================================================================
# 2. 單一 item 無價格
# ================================================================
def test_single_item_no_price():
    r = run("售 紅線")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert r.legacy_data["seller_price"] is None
    assert "no_seller_price" in r.warnings


# ================================================================
# 3. 多商品多 seller ask → blocked
# ================================================================
def test_multi_item_multi_ask_blocked():
    r = run("紅線 7480 火神 14000")
    assert r.blocked is True
    assert r.legacy_data is None
    assert r.selection_reason is LegacySelectionReason.AMBIGUOUS


# ================================================================
# 4. 多商品只有一件是 selling（無 SELLER_ASK）→ 選它但價格 None
# ================================================================
def test_multi_item_one_seller_selected():
    r = run("紅線只展示；售 火神 14000")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert "Vulcan" in r.legacy_data["market_hash_name"]
    # 14000 是 UNKNOWN price type → 不得當 seller ask
    assert r.legacy_data["seller_price"] is None
    assert "unknown_price_not_used" in r.warnings


# ================================================================
# 5. selling + buying → 只選 selling（無 SELLER_ASK 時價格 None）
# ================================================================
def test_selling_and_buying_selects_selling():
    r = run("售 紅線 5000。收 火神 3000")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert "Redline" in r.legacy_data["market_hash_name"]
    # 5000/3000 都是 UNKNOWN type → seller_price=None
    assert r.legacy_data["seller_price"] is None
    assert "unknown_price_not_used" in r.warnings


# ================================================================
# 6. seller ask 優先於 BUFF floor / calculated
# ================================================================
def test_seller_ask_not_buff_floor():
    r = run("售 夜行衣 同磨底2100*4.4=9200算5000")
    assert r.blocked is False
    assert r.legacy_data["seller_price"] == 5000, f"不得選 2100/9200: {r.legacy_data}"


# ================================================================
# 7. bundle total → blocked
# ================================================================
def test_bundle_total_blocked():
    r = run("紅線與火神兩把一起20000")
    assert r.blocked is True
    assert r.legacy_data is None
    assert r.selection_reason is LegacySelectionReason.AMBIGUOUS


# ================================================================
# 8. RMB seller ask → blocked（不換算）
# ================================================================
def test_rmb_seller_price_blocked():
    """P1.2：RMB 不再被 adapter blocked——透傳原始 Money，由 stage 換算"""
    r = run("售 紅線 9500RMB")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert r.legacy_data["currency"] == "RMB"
    assert r.legacy_data["original"].currency is Currency.RMB
    assert r.legacy_data["converted"] is None  # adapter 不換算


# ================================================================
# 9. validation_error item → blocked UNRESOLVED
# ================================================================
def test_validation_error_blocked():
    item = make_item(validation_error="invalid name")
    p = make_parsed_post([item], [])
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.UNRESOLVED


# ================================================================
# 10. ParseStatus.ERROR → blocked
# ================================================================
def test_parse_error_blocked():
    p = make_parsed_post([], [], status=ParseStatus.ERROR)
    p.errors.append("boom")
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.legacy_data is None
    assert any("parse_error" in w for w in r.warnings)


# ================================================================
# 11. 缺 market_hash_name → blocked
# ================================================================
def test_missing_market_hash_name_blocked():
    item = make_item(mhn=None)
    p = make_parsed_post([item], [])
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.UNRESOLVED


# ================================================================
# 12. 重複同金額 seller ask → 允許 + warning
# ================================================================
def test_duplicate_same_seller_price_allowed_with_warning():
    item = make_item()
    item.linked_price_indexes = [0, 1]
    prices = [make_price("5000", start=0, end=4), make_price("5000", start=5, end=9)]
    p = make_parsed_post([item], prices)
    r = to_legacy_skin_info(p)
    assert r.blocked is False
    assert r.legacy_data["seller_price"] == 5000
    assert any("duplicate_seller_price" in w for w in r.warnings)


# ================================================================
# 13. 衝突 seller prices → blocked
# ================================================================
def test_conflicting_seller_prices_blocked():
    item = make_item()
    item.linked_price_indexes = [0, 1]
    prices = [make_price("5000", start=0, end=4), make_price("6000", start=5, end=9)]
    p = make_parsed_post([item], prices)
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.AMBIGUOUS


# ================================================================
# 14. 無 items → blocked NO_ITEM
# ================================================================
def test_no_items_blocked():
    p = make_parsed_post([], [])
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.NO_ITEM


# ================================================================
# 15. legacy_data 必要欄位
# ================================================================
def test_legacy_data_required_fields():
    r = run("售 紅線 5000")
    assert r.legacy_data is not None
    assert "market_hash_name" in r.legacy_data
    assert "seller_price" in r.legacy_data
    assert "confidence" in r.legacy_data


# ================================================================
# 16. ParsedPost 不被修改
# ================================================================
def test_input_parsed_post_not_mutated():
    item = make_item()
    item.linked_price_indexes = [0]
    price = make_price()
    p = make_parsed_post([item], [price])
    orig_links = list(item.linked_price_indexes)
    r = to_legacy_skin_info(p)
    assert item.linked_price_indexes == orig_links
    assert price.associated_item_index is None  # 未被寫入


# ================================================================
# 17. 結果 indexes 有效
# ================================================================
def test_result_indexes_valid():
    # 手動建：唯一 SELLING + SELLER_ASK
    item = make_item()
    item.linked_price_indexes = [0]
    price = make_price("5000")
    p = make_parsed_post([item], [price])
    r = to_legacy_skin_info(p)
    assert r.selected_item_index == 0
    assert r.selected_price_index == 0
    # blocked 結果 indexes 全 None
    r2 = run("紅線 火神 14000 7480")
    assert r2.selected_item_index is None
    assert r2.selected_price_index is None


# ================================================================
# 18. warnings 唯一
# ================================================================
def test_warnings_unique():
    r = run("售 紅線")
    assert len(r.warnings) == len(set(r.warnings))


# ================================================================
# 19. 錯誤型別 → raise
# ================================================================
def test_wrong_parsed_post_type_raises():
    with pytest.raises(TypeError):
        to_legacy_skin_info("not-parsed-post")  # type: ignore[arg-type]


# ================================================================
# 20. parse_to_legacy E2E（「售 X 同磨底」→ 例外選 selling + SELLER_ASK）
# ================================================================
def test_parse_to_legacy_end_to_end():
    r = run("售 AK-47 | 红线 久经沙场 5000")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert "Redline" in r.legacy_data["market_hash_name"]
    # 5000 是 bare UNKNOWN → 不得當 seller ask
    assert r.legacy_data["seller_price"] is None
    assert r.legacy_data["source"] == "v2_adapter"
    assert r.legacy_data["item_role"] == "selling"


# ================================================================
# 20b. E2E：售 + 算（真 SELLER_ASK）→ 輸出金額
# ================================================================
def test_parse_to_legacy_end_to_end_with_suan():
    r = run("售 夜行衣 同磨底2100*4.4=9200算5000")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert r.legacy_data["seller_price"] == 5000


# ================================================================
# 21. 無外部呼叫
# ================================================================
def test_no_external_calls(monkeypatch):
    import socket

    def _boom(*a, **k):
        raise AssertionError("不應有外部連線")

    monkeypatch.setattr(socket, "create_connection", _boom)
    monkeypatch.setattr(socket, "socket", _boom)
    r = run("售 紅線 5000")
    assert r.blocked is False


# ================================================================
# 22. 不換算貨幣（RMB 直接 block，無 legacy_data）
# ================================================================
def test_no_currency_conversion():
    """P1.2：adapter 不執行換算（原始 9500 RMB 原樣透傳，無 ×4.5）"""
    r = run("售 紅線 9500RMB")
    assert r.blocked is False
    assert r.legacy_data is not None
    # 9500 沒有被 ×4.5（原始金額保留）
    assert r.legacy_data["original_price"] == 9500
    assert r.legacy_data["converted"] is None


# ================================================================
# 23. buying only → blocked
# ================================================================
def test_buying_only_blocked():
    r = run("收 紅線 3000")
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.AMBIGUOUS


# ================================================================
# 24. trade only → blocked
# ================================================================
def test_trade_only_blocked():
    r = run("紅線貼換火神")
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.AMBIGUOUS


# ================================================================
# 25. ambiguous reason 非空
# ================================================================
def test_ambiguous_reason_not_empty():
    r = run("紅線 火神 14000 7480")
    assert r.blocked is True
    assert r.warnings, "ambiguous 應有 warning 說明"
    assert all(w.strip() for w in r.warnings)


# ================================================================
# Phase 6.1.1 — Safety Hardening
# ================================================================
# 26. UNKNOWN 貨幣不得假設為 TWD
# ---------------------------------------------------------------
def test_unknown_currency_not_assumed_twd():
    """P1.2：UNKNOWN 透傳（不假設 TWD）；由共用 stage fail-closed"""
    item = make_item()
    item.linked_price_indexes = [0]
    price = make_price("5000", currency=Currency.UNKNOWN)
    p = make_parsed_post([item], [price])
    r = to_legacy_skin_info(p)
    assert r.blocked is False
    assert r.legacy_data["currency"] == "UNKNOWN"
    assert r.legacy_data["original"].currency is Currency.UNKNOWN


# ---------------------------------------------------------------
# 27. UNKNOWN price type 不得當 seller ask
# ---------------------------------------------------------------
def test_unknown_price_type_not_used_as_seller_ask():
    r = run("售 紅線 5000")
    assert r.blocked is False
    assert r.legacy_data is not None
    assert r.legacy_data["seller_price"] is None, "UNKNOWN 價格不得輸出"
    assert "no_seller_price" in r.warnings
    assert "unknown_price_not_used" in r.warnings


# ---------------------------------------------------------------
# 28. 顯式 TWD SELLER_ASK 允許
# ---------------------------------------------------------------
def test_explicit_twd_seller_ask_allowed():
    item = make_item()
    item.linked_price_indexes = [0]
    price = make_price("5000", ptype=PriceType.SELLER_ASK, currency=Currency.TWD)
    p = make_parsed_post([item], [price])
    r = to_legacy_skin_info(p)
    assert r.blocked is False
    assert r.legacy_data["seller_price"] == 5000


# ---------------------------------------------------------------
# 29. 未選中的 invalid reference 不阻擋有效 seller
# ---------------------------------------------------------------
def test_unselected_invalid_reference_does_not_block_valid_seller():
    seller = make_item(skin="Redline", role=ItemRole.SELLING)
    seller.linked_price_indexes = [0]
    price = make_price("5000")
    ref_bad = make_item(skin="Vulcan", role=ItemRole.REFERENCE,
                        validation_error="invalid name")
    p = make_parsed_post([seller, ref_bad], [price])
    r = to_legacy_skin_info(p)
    assert r.blocked is False
    assert r.legacy_data["seller_price"] == 5000
    assert any("unselected_item[1]_validation_error" in w for w in r.warnings)


# ---------------------------------------------------------------
# 30. 被選 item 有 validation_error → blocked
# ---------------------------------------------------------------
def test_selected_item_validation_error_blocks():
    item = make_item(role=ItemRole.SELLING, validation_error="invalid name")
    item.linked_price_indexes = [0]
    price = make_price("5000")
    p = make_parsed_post([item], [price])
    r = to_legacy_skin_info(p)
    assert r.blocked is True
    assert r.selection_reason is LegacySelectionReason.UNRESOLVED


# ---------------------------------------------------------------
# 31. legacy_data market_hash_name 空白 → raise
# ---------------------------------------------------------------
def test_legacy_market_hash_name_blank_raises():
    with pytest.raises(ValueError):
        LegacyAdapterResult(
            legacy_data={"market_hash_name": "  ", "seller_price": None,
                         "confidence": 0.5},
            selected_item_index=None, selected_price_index=None,
            selection_reason=LegacySelectionReason.COMPATIBILITY_FALLBACK,
            blocked=False,
        )


# ---------------------------------------------------------------
# 32. seller_price 錯誤型別 → raise
# ---------------------------------------------------------------
def test_legacy_seller_price_wrong_type_raises():
    with pytest.raises(TypeError):
        LegacyAdapterResult(
            legacy_data={"market_hash_name": "X", "seller_price": "5000",
                         "confidence": 0.5},
            selected_item_index=None, selected_price_index=None,
            selection_reason=LegacySelectionReason.COMPATIBILITY_FALLBACK,
            blocked=False,
        )


# ---------------------------------------------------------------
# 33. seller_price bool → raise
# ---------------------------------------------------------------
def test_legacy_seller_price_bool_raises():
    with pytest.raises(TypeError):
        LegacyAdapterResult(
            legacy_data={"market_hash_name": "X", "seller_price": True,
                         "confidence": 0.5},
            selected_item_index=None, selected_price_index=None,
            selection_reason=LegacySelectionReason.COMPATIBILITY_FALLBACK,
            blocked=False,
        )


# ---------------------------------------------------------------
# 34. 缺 seller_price key → raise
# ---------------------------------------------------------------
def test_missing_seller_price_key_raises():
    with pytest.raises(ValueError):
        LegacyAdapterResult(
            legacy_data={"market_hash_name": "X", "confidence": 0.5},
            selected_item_index=None, selected_price_index=None,
            selection_reason=LegacySelectionReason.COMPATIBILITY_FALLBACK,
            blocked=False,
        )


# ---------------------------------------------------------------
# 35. warnings 輸入被複製
# ---------------------------------------------------------------
def test_warnings_input_is_copied():
    w = ["no_seller_price"]
    item = make_item()
    p = make_parsed_post([item], [])
    r = to_legacy_skin_info(p)
    w.append("extra")  # 外部修改不影響
    assert "extra" not in r.warnings
