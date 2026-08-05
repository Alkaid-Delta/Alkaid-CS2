# -*- coding: utf-8 -*-
"""
market_candidate.py — structured market candidate（P6-R1）

從 ParsedPost 建立 deterministic market lookup candidates：
每個 candidate = 一個 verified item + 一個已連結 SELLER_ASK price。

規則（契約見 P6-R1 structured-market-candidate-contract）：
- 一個 candidate 對應一個商品與一個已連結 seller ask
- 同一 price 不得供多 item（bundle total 不進一般 lookup）
- price_type 必須 SELLER_ASK
- item 必須 verified=True
- UNKNOWN currency 標記 blocked（實際 conversion 由 P1 CurrencyService 處理）
- 未連結價格保留於 ParsedPost diagnostics——不建 candidate
- legacy dict 不是 structured 來源——ParsedPost 為唯一來源

block reason codes（machine-readable）：
P6_MARKET_CANDIDATE_ITEM_UNVERIFIED
P6_MARKET_CANDIDATE_PRICE_UNLINKED
P6_MARKET_CANDIDATE_NOT_SELLER_ASK
P6_MARKET_CANDIDATE_UNKNOWN_CURRENCY
P6_MARKET_CANDIDATE_BUNDLE_UNSUPPORTED
"""
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemRole
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.parsed_post import ParsedPost
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceType


@dataclass(frozen=True)
class MarketCandidate:
    """單一商品 + 已連結 SELLER_ASK 的 market lookup 候選。"""

    # item 側
    item_index: int
    market_hash_name: str | None
    verified: bool
    verified_by: str | None = None
    item_role: ItemRole | None = None
    item_confidence: float = 0.0
    item_evidence: str = ""

    # price 側
    price_index: int | None = None
    price_type: PriceType | None = None
    original_money: "Money | None" = None  # P1 Money 型別（不重建 Currency 模型）
    original_currency: str | None = None
    price_image_index: int | None = None
    associated_item_index: int | None = None
    price_evidence: str = ""

    # conversion（consumer 用 resolve_seller_ask_conversion 填——不在此換算）
    converted_twd: Decimal | None = None
    rate_used: Decimal | None = None
    rate_source: str | None = None

    # gate
    blocked: bool = False
    block_reason: str | None = None
    warnings: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if isinstance(self.item_index, bool) or not isinstance(self.item_index, int):
            raise TypeError("item_index 必須是 int")
        if self.item_index < 0:
            raise ValueError("item_index 不可為負數")
        if self.price_index is not None and self.price_index < 0:
            raise ValueError("price_index 不可為負數")
        if self.blocked and not self.block_reason:
            raise ValueError("blocked=True 時 block_reason 不可為空")


# machine-readable block reason codes
REASON_ITEM_UNVERIFIED = "P6_MARKET_CANDIDATE_ITEM_UNVERIFIED"
REASON_PRICE_UNLINKED = "P6_MARKET_CANDIDATE_PRICE_UNLINKED"
REASON_NOT_SELLER_ASK = "P6_MARKET_CANDIDATE_NOT_SELLER_ASK"
REASON_UNKNOWN_CURRENCY = "P6_MARKET_CANDIDATE_UNKNOWN_CURRENCY"
REASON_BUNDLE_UNSUPPORTED = "P6_MARKET_CANDIDATE_BUNDLE_UNSUPPORTED"
REASON_ASSOCIATION_MISMATCH = "P6_MARKET_CANDIDATE_ASSOCIATION_MISMATCH"
REASON_PRICE_ALREADY_OWNED = "P6_MARKET_CANDIDATE_PRICE_ALREADY_OWNED"
REASON_PRICE_INDEX_INVALID = "P6_MARKET_CANDIDATE_PRICE_INDEX_INVALID"


def _currency_str(money: Any) -> str | None:
    cur = getattr(money, "currency", None)
    return getattr(cur, "value", None) if cur is not None else None


def build_market_candidates(parsed_post: ParsedPost) -> list[MarketCandidate]:
    """從 ParsedPost 建立 deterministic structured candidates。

    - 只處理 verified item + linked SELLER_ASK price
    - 依 item_index 穩定排序（deterministic）
    - 未驗證/未連結/非 SELLER_ASK/UNKNOWN currency → blocked 或排除
    """
    if parsed_post is None:
        return []
    items = list(parsed_post.items)
    prices = list(parsed_post.prices)
    candidates: list[MarketCandidate] = []

    owned_prices: set[int] = set()
    for item_index, it in enumerate(items):
        # linked price indexes（依 ItemCandidate 契約）
        linked = list(getattr(it, "linked_price_indexes", []) or [])
        # 未驗證 item：建立 blocked candidate（fail-closed 記錄——附 linked price 資訊）
        if not it.verified:
            _price_index = linked[0] if linked and 0 <= linked[0] < len(prices) else None
            _pc = prices[_price_index] if _price_index is not None else None
            candidates.append(MarketCandidate(
                item_index=item_index,
                market_hash_name=it.market_hash_name,
                verified=False,
                verified_by=it.verified_by,
                item_role=getattr(it, "role", None),
                item_confidence=getattr(it, "confidence", 0.0),
                item_evidence=getattr(it, "evidence", "") or "",
                price_index=_price_index,
                price_type=_pc.price_type if _pc else None,
                original_money=_pc.money if _pc else None,
                original_currency=_currency_str(_pc.money) if _pc else None,
                price_image_index=_pc.image_index if _pc else None,
                associated_item_index=_pc.associated_item_index if _pc else None,
                price_evidence=getattr(_pc, "evidence", "") if _pc else "",
                blocked=True, block_reason=REASON_ITEM_UNVERIFIED,
                warnings=["item_unverified"],
            ))
            continue
        # 無 linked price：建立 blocked candidate（price unlinked）
        if not linked:
            candidates.append(MarketCandidate(
                item_index=item_index,
                market_hash_name=it.market_hash_name,
                verified=True,
                verified_by=it.verified_by,
                item_role=getattr(it, "role", None),
                item_confidence=getattr(it, "confidence", 0.0),
                item_evidence=getattr(it, "evidence", "") or "",
                price_index=None, price_type=None,
                blocked=True, block_reason=REASON_PRICE_UNLINKED,
                warnings=["price_unlinked"],
            ))
            continue
        # 每個 linked price（通常 1 個；多個則各建 candidate——同一 price 不供多 item）
        for price_index in linked:
            if price_index < 0 or price_index >= len(prices):
                # invalid linked index：不得靜默忽略——blocked diagnostics
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index if price_index >= 0 else None,
                    price_type=None,
                    blocked=True, block_reason=REASON_PRICE_INDEX_INVALID,
                    warnings=["price_index_invalid"],
                ))
                continue
            pc = prices[price_index]
            # 同一 price 不得被多個 item 使用（ownership——優先於 association）
            if price_index in owned_prices:
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=_currency_str(pc.money),
                    price_image_index=pc.image_index,
                    associated_item_index=pc.associated_item_index,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_PRICE_ALREADY_OWNED,
                    warnings=["price_already_owned"],
                ))
                continue
            # linkage hard gate：associated_item_index 必須 == item_index
            if pc.associated_item_index is not None and pc.associated_item_index != item_index:
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=_currency_str(pc.money),
                    price_image_index=pc.image_index,
                    associated_item_index=pc.associated_item_index,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_ASSOCIATION_MISMATCH,
                    warnings=["association_mismatch"],
                ))
                continue
            # BUNDLE_TOTAL：先於 NOT_SELLER_ASK 判定（專屬 reason）
            if pc.price_type is PriceType.BUNDLE_TOTAL:
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=_currency_str(pc.money),
                    price_image_index=pc.image_index,
                    associated_item_index=pc.associated_item_index,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_BUNDLE_UNSUPPORTED,
                    warnings=["bundle_total_unsupported"],
                ))
                continue
            # 非 SELLER_ASK → blocked（價格保留於 diagnostics）
            if pc.price_type is not PriceType.SELLER_ASK:
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=_currency_str(pc.money),
                    price_image_index=pc.image_index,
                    associated_item_index=pc.associated_item_index,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_NOT_SELLER_ASK,
                    warnings=["not_seller_ask"],
                ))
                continue
            # 舊 bundle 分支（移除——已前置）
            if pc.associated_item_index is None:
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=_currency_str(pc.money),
                    price_image_index=pc.image_index,
                    associated_item_index=None,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_PRICE_UNLINKED,
                    warnings=["price_unlinked"],
                ))
                continue
            # UNKNOWN currency → blocked（fail-closed）
            cur = _currency_str(pc.money)
            if cur is None or cur == "UNKNOWN":
                candidates.append(MarketCandidate(
                    item_index=item_index,
                    market_hash_name=it.market_hash_name,
                    verified=True,
                    verified_by=it.verified_by,
                    item_role=getattr(it, "role", None),
                    item_confidence=getattr(it, "confidence", 0.0),
                    item_evidence=getattr(it, "evidence", "") or "",
                    price_index=price_index,
                    price_type=pc.price_type,
                    original_money=pc.money,
                    original_currency=cur,
                    price_image_index=pc.image_index,
                    associated_item_index=pc.associated_item_index,
                    price_evidence=getattr(pc, "evidence", "") or "",
                    blocked=True, block_reason=REASON_UNKNOWN_CURRENCY,
                    warnings=["unknown_currency"],
                ))
                continue
            # eligible：verified + linked + SELLER_ASK + 已知 currency
            owned_prices.add(price_index)
            candidates.append(MarketCandidate(
                item_index=item_index,
                market_hash_name=it.market_hash_name,
                verified=True,
                verified_by=it.verified_by,
                item_role=getattr(it, "role", None),
                item_confidence=getattr(it, "confidence", 0.0),
                item_evidence=getattr(it, "evidence", "") or "",
                price_index=price_index,
                price_type=pc.price_type,
                original_money=pc.money,
                original_currency=cur,
                price_image_index=pc.image_index,
                associated_item_index=pc.associated_item_index,
                price_evidence=getattr(pc, "evidence", "") or "",
                blocked=False,
            ))
    return candidates


def eligible_candidates(candidates: list[MarketCandidate]) -> list[MarketCandidate]:
    """僅回傳可進入 market lookup 的 candidates（verified + SELLER_ASK + 非 blocked）。"""
    return [c for c in candidates if not c.blocked and c.verified
            and c.price_type is PriceType.SELLER_ASK]
