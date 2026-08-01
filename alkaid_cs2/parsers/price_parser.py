"""
price_parser.py — 純文字價格解析器（V2 Phase 2）

第一版只使用 deterministic regex，不呼叫任何模型、不進行匯率換算。

支援：
  A. seller ask: 算5000 / 算你5000 / 開價5000 / 售5000 / 賣5000 / 5000帶走
  B. BUFF floor: 同磨底2100 / BUFF底2100
  C. calculation: 2100*4.4=9200 / 2100×4.4=9200（左=參考/底, 右=CALCULATED）
  D. explicit currency: 9500RMB / 9500 RMB / ¥9500 / NT$5000 / 5000TWD
  E. bundle total: 兩把一起5000 / 兩件打包5000 / 全收5000
  F. bare number（低信心，僅 3+ 位整數，排除 float 與貼紙數量）

規則：
- 收集所有價格，不得第一命中 return
- 同一數字不重複產生候選（以文字位置去重，保留優先級最高者）
- 未標示幣別的 seller ask 預設 TWD
- BUFF floor 預設 RMB
- 計算式結果（=右側）預設 TWD
"""
import re
from decimal import Decimal

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType

_NUM = r"(?:\d{1,3}(?:,\d{3})+|\d{3,})"  # 1,000 千分位 或 3+ 位連續數字
_CURRENCY_SUFFIX = r"(?:RMB|TWD|USD|NTD|NT)"

# 同位置候選的優先級（數值越高越優先）
_TYPE_PRIORITY = {
    PriceType.BUFF_FLOOR: 60,
    PriceType.SELLER_ASK: 50,
    PriceType.BUNDLE_TOTAL: 45,
    PriceType.CALCULATED: 40,
    PriceType.REFERENCE: 30,
    PriceType.UNKNOWN: 10,
}

_CURRENCY_PRIORITY = {
    Currency.RMB: 3,
    Currency.TWD: 3,
    Currency.USD: 3,
    Currency.UNKNOWN: 1,
}


def _clean_num(raw: str) -> Decimal:
    return Decimal(raw.replace(",", ""))


def _is_sell_context(text: str, pos: int) -> bool:
    """檢查位置前方是否有賣家語意（售/賣/算/開價）"""
    window = text[max(0, pos - 8):pos]
    return any(k in window for k in ("售", "賣", "算", "開價", "帶走"))


def _is_buff_floor_context(text: str, pos: int) -> bool:
    """檢查位置前方是否有 BUFF 底價語意（同磨底/BUFF底）"""
    window = text[max(0, pos - 6):pos]
    return "同磨底" in window or "BUFF底" in window or "buff底" in window


class _Collector:
    """收集候選並以 (text_start, text_end) 去重，保留優先級最高者。"""

    def __init__(self, text: str):
        self.text = text
        self._by_pos: dict[tuple[int, int], PriceCandidate] = {}

    def _priority(self, cand: PriceCandidate) -> int:
        return _TYPE_PRIORITY[cand.price_type] + _CURRENCY_PRIORITY[cand.money.currency]

    def add(self, amount: Decimal, currency: Currency, ptype: PriceType,
            source: PriceSource, evidence: str, start: int, end: int,
            confidence: float) -> None:
        key = (start, end)
        cand = PriceCandidate(
            money=Money(amount, currency),
            price_type=ptype,
            source=source,
            evidence=evidence,
            confidence=confidence,
            text_start=start,
            text_end=end,
        )
        prev = self._by_pos.get(key)
        if prev is None or self._priority(cand) > self._priority(prev):
            self._by_pos[key] = cand

    def result(self) -> list[PriceCandidate]:
        return sorted(self._by_pos.values(), key=lambda c: c.text_start or 0)


def parse_price_candidates(text: str) -> list[PriceCandidate]:
    if not text or not text.strip():
        return []

    col = _Collector(text)

    # ── A. seller ask（排除後接顯式幣別，避免與 D 衝突）──
    for m in re.finditer(
        rf"(?:算|算你|開價|售|賣|出)\s*({_NUM})(?![0-9])(?!\s*{_CURRENCY_SUFFIX})",
        text,
    ):
        num_start = text.find(m.group(1), m.start())
        num_end = num_start + len(m.group(1))
        col.add(_clean_num(m.group(1)), Currency.TWD, PriceType.SELLER_ASK,
                PriceSource.TEXT, m.group(0), num_start, num_end, 0.9)
    for m in re.finditer(rf"({_NUM})\s*帶走", text):
        col.add(_clean_num(m.group(1)), Currency.TWD, PriceType.SELLER_ASK,
                PriceSource.TEXT, m.group(0), m.start(1), m.end(1), 0.85)

    # ── B. BUFF floor ──
    for m in re.finditer(rf"(?:同磨底|BUFF底|buff底)\s*({_NUM})", text):
        # 位置指向數字核心（去掉「同磨底」前綴），與 C regex 的數字位置一致以去重
        num_start = text.find(m.group(1), m.start())
        num_end = num_start + len(m.group(1))
        col.add(_clean_num(m.group(1)), Currency.RMB, PriceType.BUFF_FLOOR,
                PriceSource.TEXT, m.group(0), num_start, num_end, 0.9)

    # ── C. calculation: 左×倍率=右 ──
    for m in re.finditer(
        rf"({_NUM})\s*[×*xX]\s*\d+(?:\.\d+)?\s*=\s*({_NUM})",
        text,
    ):
        left_start = m.start(1)
        ltype = PriceType.BUFF_FLOOR if _is_buff_floor_context(text, left_start) else PriceType.REFERENCE
        col.add(_clean_num(m.group(1)), Currency.RMB, ltype,
                PriceSource.CALCULATION, m.group(1), m.start(1), m.end(1), 0.85)
        col.add(_clean_num(m.group(2)), Currency.TWD, PriceType.CALCULATED,
                PriceSource.CALCULATION, m.group(2), m.start(2), m.end(2), 0.85)

    # ── D. explicit currency（位置一律指向數字核心，與 bare number 共用 dedup key）──
    # 9500RMB / 9500 RMB / 5000TWD
    for m in re.finditer(rf"({_NUM})\s*({_CURRENCY_SUFFIX})", text):
        suffix = m.group(2).upper()
        currency = (Currency.RMB if suffix in ("RMB",)
                    else Currency.TWD if suffix in ("TWD", "NTD", "NT")
                    else Currency.USD)
        ptype = PriceType.SELLER_ASK if _is_sell_context(text, m.start()) else PriceType.REFERENCE
        col.add(_clean_num(m.group(1)), currency, ptype,
                PriceSource.TEXT, m.group(0), m.start(1), m.end(1), 0.85)
    # ¥9500 / ￥9500 → RMB
    for m in re.finditer(rf"[¥￥]\s*({_NUM})", text):
        ptype = PriceType.SELLER_ASK if _is_sell_context(text, m.start()) else PriceType.REFERENCE
        col.add(_clean_num(m.group(1)), Currency.RMB, ptype,
                PriceSource.TEXT, m.group(0), m.start(1), m.end(1), 0.85)
    # NT$5000 → TWD
    for m in re.finditer(rf"NT\$\s*({_NUM})", text):
        ptype = PriceType.SELLER_ASK if _is_sell_context(text, m.start()) else PriceType.REFERENCE
        col.add(_clean_num(m.group(1)), Currency.TWD, ptype,
                PriceSource.TEXT, m.group(0), m.start(1), m.end(1), 0.85)
    # $5000 → 語境判定：全文含 TWD/NT/台幣 → TWD，否則 UNKNOWN
    for m in re.finditer(rf"\$\s*({_NUM})", text):
        has_twd_ctx = any(k in text for k in ("TWD", "NT", "台幣", "新台幣"))
        currency = Currency.TWD if has_twd_ctx else Currency.UNKNOWN
        ptype = PriceType.SELLER_ASK if _is_sell_context(text, m.start()) else PriceType.REFERENCE
        col.add(_clean_num(m.group(1)), currency, ptype,
                PriceSource.TEXT, m.group(0), m.start(1), m.end(1), 0.75)

    # ── E. bundle total ──
    for m in re.finditer(
        rf"(?:兩把|兩件|兩支|整套|全部|全包|打包|一起|全收)\s*({_NUM})",
        text,
    ):
        num_start = text.find(m.group(1), m.start())
        num_end = num_start + len(m.group(1))
        col.add(_clean_num(m.group(1)), Currency.TWD, PriceType.BUNDLE_TOTAL,
                PriceSource.TEXT, m.group(0), num_start, num_end, 0.8)

    # ── F. bare number（低信心，3+ 位整數，排除已被覆蓋位置）──
    for m in re.finditer(r"(?<![0-9.])[0-9]{3,}(?![0-9.])", text):
        start, end = m.start(), m.end()
        if (start, end) in col._by_pos:
            continue
        col.add(Decimal(m.group(0)), Currency.UNKNOWN, PriceType.UNKNOWN,
                PriceSource.TEXT, m.group(0), start, end, 0.3)

    return col.result()
