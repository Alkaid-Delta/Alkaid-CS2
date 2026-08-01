"""
test_price_parser.py — 價格解析器測試（Phase 2）

驗證 parse_price_candidates 的 deterministic regex 行為。
不呼叫模型、不換算匯率。
"""
import sys
import os
from decimal import Decimal

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.enums import Currency  # noqa: E402
from alkaid_cs2.domain.price_candidate import PriceType  # noqa: E402
from alkaid_cs2.parsers.price_parser import parse_price_candidates  # noqa: E402


def prices_of(text: str) -> list[tuple[Decimal, Currency, PriceType]]:
    return [(p.money.amount, p.money.currency, p.price_type)
            for p in parse_price_candidates(text)]


# ---------------------------------------------------------------
# 1. 算5000 → SELLER_ASK 5000 TWD
# ---------------------------------------------------------------
def test_suan_5000():
    ps = prices_of("急售沙鷹 算5000")
    assert (Decimal("5000"), Currency.TWD, PriceType.SELLER_ASK) in ps


# ---------------------------------------------------------------
# 2. 算你5000 → SELLER_ASK 5000 TWD
# ---------------------------------------------------------------
def test_suanni_5000():
    ps = prices_of("售 夜行衣 算你5000")
    assert (Decimal("5000"), Currency.TWD, PriceType.SELLER_ASK) in ps


# ---------------------------------------------------------------
# 3. 同磨底2100 → BUFF_FLOOR 2100 RMB
# ---------------------------------------------------------------
def test_tongmodi_2100():
    ps = prices_of("售 久經邁阿密 同磨底2100")
    assert (Decimal("2100"), Currency.RMB, PriceType.BUFF_FLOOR) in ps


# ---------------------------------------------------------------
# 4. 2100*4.4=9200 → REFERENCE 2100 RMB + CALCULATED 9200 TWD
# ---------------------------------------------------------------
def test_calculation_expression():
    ps = prices_of("2100*4.4=9200")
    assert (Decimal("2100"), Currency.RMB, PriceType.REFERENCE) in ps
    assert (Decimal("9200"), Currency.TWD, PriceType.CALCULATED) in ps


# ---------------------------------------------------------------
# 5. 同磨底2100*4.4=9200算5000 → 三種價格
# ---------------------------------------------------------------
def test_full_expression():
    ps = prices_of("售 夜行衣 同磨底2100*4.4=9200算5000")
    assert (Decimal("2100"), Currency.RMB, PriceType.BUFF_FLOOR) in ps
    assert (Decimal("9200"), Currency.TWD, PriceType.CALCULATED) in ps
    assert (Decimal("5000"), Currency.TWD, PriceType.SELLER_ASK) in ps


# ---------------------------------------------------------------
# 6. 9500RMB → REFERENCE 9500 RMB（無售語境）
# ---------------------------------------------------------------
def test_rmb_suffix():
    ps = prices_of("9500RMB")
    assert (Decimal("9500"), Currency.RMB, PriceType.REFERENCE) in ps


# ---------------------------------------------------------------
# 7. NT$5000 → REFERENCE 5000 TWD（無售語境）
# ---------------------------------------------------------------
def test_ntd_prefix():
    ps = prices_of("NT$5000")
    assert (Decimal("5000"), Currency.TWD, PriceType.REFERENCE) in ps


# ---------------------------------------------------------------
# 8. 兩件打包5000 → BUNDLE_TOTAL 5000 TWD
# ---------------------------------------------------------------
def test_bundle_total():
    ps = prices_of("兩件打包5000")
    assert (Decimal("5000"), Currency.TWD, PriceType.BUNDLE_TOTAL) in ps


# ---------------------------------------------------------------
# 9. 多價格全部保留
# ---------------------------------------------------------------
def test_multiple_prices_kept():
    ps = prices_of("售 A 5000 收 B 3000 全包 12000")
    amounts = {p[0] for p in ps}
    assert Decimal("5000") in amounts
    assert Decimal("3000") in amounts
    assert Decimal("12000") in amounts
    assert len(ps) >= 3


# ---------------------------------------------------------------
# 10. 不重複候選（同位置只保留一筆）
# ---------------------------------------------------------------
def test_no_duplicate_candidates():
    ps = parse_price_candidates("同磨底2100*4.4=9200")
    # 2100 位置只應有一筆（BUFF_FLOOR 或 REFERENCE，不會兩個都有）
    at_2100 = [p for p in ps if p.money.amount == Decimal("2100")]
    assert len(at_2100) == 1


# ---------------------------------------------------------------
# 11. 空字串 → []
# ---------------------------------------------------------------
def test_empty_text():
    assert parse_price_candidates("") == []
    assert parse_price_candidates("   ") == []


# ---------------------------------------------------------------
# 12. float 0.1234 不得誤判成價格
# ---------------------------------------------------------------
def test_float_not_price():
    ps = prices_of("蝴蝶刀 漸層 float 0.1234")
    assert ps == []


# ---------------------------------------------------------------
# 13. 貼紙數量 4xTitan 不得誤判成價格
# ---------------------------------------------------------------
def test_sticker_count_not_price():
    ps = prices_of("14卡托 4xTitan 貼紙 紅線")
    # 4（xTitan 的數量）不得被當價格；若無其他 3+ 位數字 → 空
    assert ps == []


# ---------------------------------------------------------------
# 14. 售9500RMB → SELLER_ASK 9500 RMB（售語境 + 顯式幣別）
# ---------------------------------------------------------------
def test_sell_with_rmb_suffix():
    ps = prices_of("售9500RMB")
    assert (Decimal("9500"), Currency.RMB, PriceType.SELLER_ASK) in ps


# ---------------------------------------------------------------
# 15. 證據與位置完整性
# ---------------------------------------------------------------
def test_evidence_and_position():
    text = "售 夜行衣 同磨底2100*4.4=9200算5000"
    ps = parse_price_candidates(text)
    for p in ps:
        # evidence 必須是原文片段
        assert p.evidence in text
        # 位置指向數字核心（可能小於完整 evidence，如「同磨底2100」→ 位置指向 2100）
        core = text[p.text_start:p.text_end]
        assert core.isdigit() or "," in core, f"位置未指向數字: {core!r}"
        assert core.replace(",", "") == str(p.money.amount)


# ---------------------------------------------------------------
# 16-20. 顯式幣別不得與裸數字重複（每個只有一個候選）
# ---------------------------------------------------------------
@pytest.mark.parametrize("text,amount,currency", [
    ("9500RMB", "9500", Currency.RMB),
    ("NT$5000", "5000", Currency.TWD),
    ("¥9500", "9500", Currency.RMB),
    ("$5000", "5000", Currency.UNKNOWN),  # 無語境 → UNKNOWN
    ("5000TWD", "5000", Currency.TWD),
])
def test_currency_marked_single_candidate(text, amount, currency):
    ps = parse_price_candidates(text)
    # 只有一個候選（顯式幣別覆蓋裸數字，不重複）
    assert len(ps) == 1, f"{text}: {ps}"
    assert ps[0].money.amount == Decimal(amount)
    assert ps[0].money.currency is currency


# ---------------------------------------------------------------
# 21. 售語境 + 幣別：售9500RMB 只有一個候選且為 SELLER_ASK
# ---------------------------------------------------------------
def test_sell_currency_single_candidate():
    ps = parse_price_candidates("售9500RMB")
    assert len(ps) == 1
    assert ps[0].money.amount == Decimal("9500")
    assert ps[0].money.currency is Currency.RMB
    assert ps[0].price_type is PriceType.SELLER_ASK
