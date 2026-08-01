"""
test_price_item_linker.py — 商品與價格關聯器測試（Phase 4）

驗證：位置配對、角色/型別加分、一對一規則、bundle total、
ambiguous 判定、輸入不被污染、輸入驗證。
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.domain.price import Money  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType  # noqa: E402
from alkaid_cs2.parsers.item_parser import parse_item_candidates  # noqa: E402
from alkaid_cs2.parsers.price_parser import parse_price_candidates  # noqa: E402
from alkaid_cs2.services.price_item_linker import (  # noqa: E402
    LinkDecision,
    LinkResult,
    link_prices_to_items,
)

# ── 測試字典（同 Phase 3）──
FULL_DICT = {"AK-47 | 红线": "AK-47 | Redline"}
PATTERN_DICT = {
    "红线": "Redline", "紅線": "Redline", "火神": "Vulcan",
    "鈷分裂": "Cobalt Disruption", "電擊": "Electric Hive", "夜行衣": "Nocts",
}
WEAPON_MAP = {
    "AK-47": "AK-47", "ak": "AK-47", "沙鷹": "Desert Eagle",
    "AWP": "AWP", "爪子刀": "Karambit",
}


def parse_items(text):
    return parse_item_candidates(
        text, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP
    )


def parse_prices(text):
    return parse_price_candidates(text)


def link(text, items=None, prices=None):
    if items is None:
        items = parse_items(text)
    if prices is None:
        prices = parse_prices(text)
    return link_prices_to_items(text, items, prices)


def make_price(amount, currency, ptype, text, start, end, conf=0.8):
    return PriceCandidate(
        money=Money(Decimal(amount), currency),
        price_type=ptype,
        source=PriceSource.TEXT,
        evidence=text[start:end],
        confidence=conf,
        text_start=start,
        text_end=end,
    )


def linked_prices_of(result, skin: str) -> list[PriceCandidate]:
    """回傳某 skin 商品連結的所有價格"""
    for i, it in enumerate(result.items):
        if it.skin == skin:
            return [result.prices[j] for j in it.linked_price_indexes]
    return []


# ================================================================
# 1. 兩商品兩價格
# ================================================================
def test_two_items_two_prices():
    text = "出2把傳家寶ak 14卡托紅線 7480 火神4xtitan 14000"
    result = link(text)

    red_prices = linked_prices_of(result, "Redline")
    vul_prices = linked_prices_of(result, "Vulcan")
    assert any(p.money.amount == Decimal("7480") for p in red_prices), f"red={red_prices}"
    assert any(p.money.amount == Decimal("14000") for p in vul_prices), f"vul={vul_prices}"
    # 不得錯配
    assert not any(p.money.amount == Decimal("14000") for p in red_prices)


# ================================================================
# 2. seller ask + BUFF floor + calculated 同一商品
# ================================================================
def test_three_price_types_one_item():
    text = "售 夜行衣 同磨底2100*4.4=9200算5000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)

    nocts_prices = linked_prices_of(result, "Nocts")
    amounts = {p.money.amount for p in nocts_prices}
    assert Decimal("2100") in amounts, f"amounts={amounts}"
    assert Decimal("9200") in amounts, f"amounts={amounts}"
    assert Decimal("5000") in amounts, f"amounts={amounts}"
    assert len(nocts_prices) == 3, f"只應綁一商品 3 價格: {nocts_prices}"


# ================================================================
# 3. selling + buying 隔離
# ================================================================
def test_selling_and_buying_isolation():
    text = "售 沙鷹鈷分裂 5000。收 AWP 電擊 3000"
    result = link(text)

    cobalts = linked_prices_of(result, "Cobalt Disruption")
    hives = linked_prices_of(result, "Electric Hive")
    assert any(p.money.amount == Decimal("5000") for p in cobalts)
    assert any(p.money.amount == Decimal("3000") for p in hives)
    assert not any(p.money.amount == Decimal("3000") for p in cobalts)


# ================================================================
# 4. reference 不污染 seller
# ================================================================
def test_reference_isolation():
    text = "售 紅線 5000；火神僅供參考 14000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)

    red = linked_prices_of(result, "Redline")
    vul = linked_prices_of(result, "Vulcan")
    assert any(p.money.amount == Decimal("5000") for p in red)
    assert any(p.money.amount == Decimal("14000") for p in vul)
    assert not any(p.money.amount == Decimal("14000") for p in red)


# ================================================================
# 5. 第二件無價格
# ================================================================
def test_second_item_without_price():
    text = "售 紅線 5000；火神只展示"
    result = link(text)
    red = linked_prices_of(result, "Redline")
    vul = linked_prices_of(result, "Vulcan")
    assert any(p.money.amount == Decimal("5000") for p in red)
    assert vul == []
    assert result.unlinked_item_indexes, "火神應在 unlinked"


# ================================================================
# 6. 第一件無價格
# ================================================================
def test_first_item_without_price():
    text = "紅線只展示；售 火神 14000"
    result = link(text)
    red = linked_prices_of(result, "Redline")
    vul = linked_prices_of(result, "Vulcan")
    assert red == []
    assert any(p.money.amount == Decimal("14000") for p in vul)


# ================================================================
# 7. bundle total 不綁單一商品
# ================================================================
def test_bundle_total_unassigned():
    text = "紅線與火神兩把一起20000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)

    bundle = [p for p in result.prices if p.price_type is PriceType.BUNDLE_TOTAL]
    assert bundle, "應有 BUNDLE_TOTAL 價格"
    assert bundle[0].associated_item_index is None
    # decision.item_index=None
    bd = [d for d in result.decisions if d.price_index == result.prices.index(bundle[0])]
    assert bd and bd[0].item_index is None
    assert any("bundle" in w for w in result.warnings)


# ================================================================
# 8. ambiguous 不自動綁定
# ================================================================
def test_ambiguous_price_unassigned():
    text = "紅線 火神 5000"
    result = link(text)
    # 5000 不得綁定任何商品
    assert all(p.associated_item_index is None for p in result.prices)
    d = result.decisions[0]
    assert d.ambiguous, f"decision={d}"


# ================================================================
# 9. 不同句隔離
# ================================================================
def test_cross_sentence_not_linked():
    text = "售 紅線。今天行情很好。火神參考14000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)
    red = linked_prices_of(result, "Redline")
    assert not any(p.money.amount == Decimal("14000") for p in red)


# ================================================================
# 10. 多價格同商品
# ================================================================
def test_multiple_prices_one_item():
    text = "售 紅線 BUFF底2100 參考9200 算5000"
    items = parse_items(text)
    r_idx = text.find("紅線")
    # 手動建價格（明確型別）
    prices = [
        make_price("2100", Currency.RMB, PriceType.BUFF_FLOOR, text,
                   text.find("2100"), text.find("2100") + 4),
        make_price("9200", Currency.TWD, PriceType.REFERENCE, text,
                   text.find("9200"), text.find("9200") + 4),
        make_price("5000", Currency.TWD, PriceType.SELLER_ASK, text,
                   text.find("5000"), text.find("5000") + 4),
    ]
    result = link(text, items, prices)
    red = linked_prices_of(result, "Redline")
    types = {p.price_type for p in red}
    assert PriceType.BUFF_FLOOR in types
    assert PriceType.REFERENCE in types
    assert PriceType.SELLER_ASK in types
    assert len(red) == 3


# ================================================================
# 11. 輸入不被污染（deepcopy）
# ================================================================
def test_input_not_mutated():
    text = "售 紅線 5000；火神 14000"
    items = parse_items(text)
    prices = parse_prices(text)
    # 記錄原始值
    orig_links = [list(it.linked_price_indexes) for it in items]
    orig_assoc = [p.associated_item_index for p in prices]

    result = link(text, items, prices)

    # 原物件不變
    assert [list(it.linked_price_indexes) for it in items] == orig_links
    assert [p.associated_item_index for p in prices] == orig_assoc
    # 結果是修改後副本
    assert result.items is not items


# ================================================================
# 12. linked_indexes 遞增排序且不重複
# ================================================================
def test_linked_indexes_sorted_unique():
    text = "售 夜行衣 同磨底2100*4.4=9200算5000"
    result = link(text)
    for it in result.items:
        idxs = it.linked_price_indexes
        assert idxs == sorted(idxs), f"未排序: {idxs}"
        assert len(set(idxs)) == len(idxs), f"重複: {idxs}"


# ================================================================
# 13-14. 輸入型別錯誤 → raise
# ================================================================
def test_wrong_item_type_raises():
    with pytest.raises(TypeError):
        link("售 紅線 5000", items=["not-item"], prices=[])


def test_wrong_price_type_raises():
    with pytest.raises(TypeError):
        link("售 紅線 5000", items=[], prices=["not-price"])


# ---------------------------------------------------------------
# 15. 無效位置 → raise
# ---------------------------------------------------------------
def test_invalid_position_raises():
    # domain 層：match_end 超過 original_text → ValueError
    with pytest.raises(ValueError):
        ItemCandidate(
            skin="Redline", role=ItemRole.SELLING, original_text="售 紅線 5000",
            match_start=999, match_end=1000, parser="test",
            evidence=ItemEvidence.DICT_PATTERN, confidence=0.8, score=50,
        )

    # linker 層：item 位置超出傳入的 text 範圍 → ValueError
    item = ItemCandidate(
        skin="Redline", role=ItemRole.SELLING, original_text="售 紅線 5000",
        match_start=0, match_end=5, parser="test",
        evidence=ItemEvidence.DICT_PATTERN, confidence=0.8, score=50,
    )
    with pytest.raises(ValueError):
        link_prices_to_items("短", items=[item], prices=[])


# ================================================================
# 16. empty items
# ================================================================
def test_empty_items():
    result = link("售 紅線 5000", items=[], prices=parse_prices("售 紅線 5000"))
    assert result.items == []
    assert result.unlinked_price_indexes == [0]


# ================================================================
# 17. empty prices
# ================================================================
def test_empty_prices():
    result = link("售 紅線 5000", items=parse_items("售 紅線 5000"), prices=[])
    assert result.prices == []
    assert len(result.unlinked_item_indexes) == len(result.items)


# ================================================================
# 18. empty text（合法 str，無候選）
# ================================================================
def test_empty_text():
    result = link("", items=[], prices=[])
    assert result.items == []
    assert result.prices == []
    assert result.decisions == []


# ================================================================
# 19. UNKNOWN 價格低信心不亂綁
# ================================================================
def test_unknown_price_low_confidence():
    text = "售 紅線 5000"
    items = parse_items(text)
    # 無位置的 UNKNOWN 價格 → 不得綁
    orphan = PriceCandidate(
        money=Money(Decimal("999"), Currency.UNKNOWN),
        price_type=PriceType.UNKNOWN,
        source=PriceSource.TEXT,
        evidence="999",
        confidence=0.3,
        text_start=None,
        text_end=None,
    )
    result = link(text, items, prices=[orphan])
    assert orphan.associated_item_index is None
    assert all(p.associated_item_index is None for p in result.prices)


# ================================================================
# 20. decision reason 非空
# ================================================================
def test_decision_reason_not_empty():
    text = "售 紅線 5000；火神 14000"
    result = link(text)
    assert result.decisions, "應有 decisions"
    for d in result.decisions:
        assert isinstance(d, LinkDecision)
        assert d.reason and d.reason.strip(), f"空 reason: {d}"


# ================================================================
# 21. LinkResult 型別與欄位完整性
# ================================================================
def test_link_result_fields():
    text = "售 紅線 5000"
    result = link(text)
    assert isinstance(result, LinkResult)
    assert isinstance(result.decisions, list)
    assert isinstance(result.unlinked_item_indexes, list)
    assert isinstance(result.unlinked_price_indexes, list)
    assert isinstance(result.warnings, list)


# ================================================================
# Phase 4.1 — Linker Hardening
# ================================================================
# 22. 單商品跨句：5000 不得連到紅線
# ---------------------------------------------------------------
def test_single_item_cross_sentence_seller_price_not_linked():
    text = "售 紅線。今天行情很好。算5000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)

    red = linked_prices_of(result, "Redline")
    assert red == [], f"跨句價格不得連結: {red}"
    assert all(p.associated_item_index is None for p in result.prices)


# ---------------------------------------------------------------
# 23. 單商品跨換行：5000 不得連到紅線
# ---------------------------------------------------------------
def test_single_item_cross_newline_price_not_linked():
    text = "售 紅線\n今天行情很好\n算5000"
    items = parse_items(text)
    prices = parse_prices(text)
    result = link(text, items, prices)

    red = linked_prices_of(result, "Redline")
    assert red == [], f"跨換行價格不得連結: {red}"
    assert all(p.associated_item_index is None for p in result.prices)


# ---------------------------------------------------------------
# 24. 無位置 item 不 crash、不被配對
# ---------------------------------------------------------------
def test_item_without_position_does_not_crash():
    text = "售 紅線 5000"
    items = parse_items(text)
    no_pos = ItemCandidate(
        skin="Vulcan", role=ItemRole.SELLING, original_text=text,
        match_start=None, match_end=None, parser="test",
        evidence=ItemEvidence.DICT_PATTERN, confidence=0.8, score=50,
    )
    items.append(no_pos)
    prices = parse_prices(text)

    result = link(text, items, prices)  # 不得 TypeError
    # 無位置 item 不被配對
    vulcan = [it for it in result.items if it.skin == "Vulcan"][0]
    assert vulcan.linked_price_indexes == []
    assert result.unlinked_item_indexes, "無位置 item 應在 unlinked"


# ---------------------------------------------------------------
# 25. 全部 item 無位置 → 所有 price unlinked
# ---------------------------------------------------------------
def test_all_items_without_position_unlinked():
    text = "售 紅線 5000"
    items = [
        ItemCandidate(skin="Redline", role=ItemRole.SELLING, original_text=text,
                      match_start=None, match_end=None, parser="test",
                      evidence=ItemEvidence.DICT_PATTERN, confidence=0.8, score=50),
        ItemCandidate(skin="Vulcan", role=ItemRole.SELLING, original_text=text,
                      match_start=None, match_end=None, parser="test",
                      evidence=ItemEvidence.DICT_PATTERN, confidence=0.8, score=50),
    ]
    prices = parse_prices(text)
    result = link(text, items, prices)
    assert all(p.associated_item_index is None for p in result.prices)
    assert len(result.unlinked_item_indexes) == 2


# ---------------------------------------------------------------
# 26-33. LinkDecision 驗證
# ---------------------------------------------------------------
def test_link_decision_negative_item_index_raises():
    with pytest.raises(ValueError):
        LinkDecision(item_index=-1, price_index=0, score=50.0, reason="x")


def test_link_decision_negative_price_index_raises():
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=-1, score=50.0, reason="x")


def test_link_decision_bool_index_raises():
    with pytest.raises(TypeError):
        LinkDecision(item_index=True, price_index=0, score=50.0, reason="x")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        LinkDecision(item_index=0, price_index=True, score=50.0, reason="x")  # type: ignore[arg-type]


def test_link_decision_nan_score_raises():
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=0, score=float("nan"), reason="x")


def test_link_decision_infinite_score_raises():
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=0, score=float("inf"), reason="x")


def test_link_decision_empty_reason_raises():
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=0, score=50.0, reason="")
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=0, score=50.0, reason="   ")


def test_link_decision_ambiguous_requires_none_item():
    with pytest.raises(ValueError):
        LinkDecision(item_index=0, price_index=0, score=50.0, reason="x", ambiguous=True)


def test_link_decision_ambiguous_type_must_be_bool():
    with pytest.raises(TypeError):
        LinkDecision(item_index=None, price_index=0, score=50.0, reason="x", ambiguous=1)  # type: ignore[arg-type]


# ---------------------------------------------------------------
# 34. 正常 LinkDecision（ambiguous=True 且 item_index=None）
# ---------------------------------------------------------------
def test_link_decision_ambiguous_ok():
    d = LinkDecision(item_index=None, price_index=0, score=50.0, reason="x", ambiguous=True)
    assert d.ambiguous is True
    assert d.item_index is None
