"""
evidence_merger.py — 文字與多圖片證據合併（V2 Phase 6.3B）

把 deterministic text ParsedPost 與多張 ImageEvidence 合併為單一 ParsedPost：
- 保留所有來源（text / image / text+image）
- 避免重複候選（去重圖片 + 等價合併）
- 標記衝突（wear / price / currency / role / name）
- 圖片預關聯（同圖 1:1 / 1:N；N:N → AMBIGUOUS_LINK）

限制：不修改輸入（deepcopy）、不呼叫模型、不查外部 API、不換算貨幣。
"""
import copy

from alkaid_cs2.domain.evidence_merge import (
    ConflictType,
    EvidenceConflict,
    MergedEvidenceResult,
)
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemRole
from alkaid_cs2.domain.parsed_post import ParsedPost, ParseStatus
from alkaid_cs2.domain.price_candidate import PriceType
from alkaid_cs2.services.image_deduplicator import deduplicate_image_evidence
from alkaid_cs2.services.price_item_linker import link_prices_to_items


# ============================================================
# Item 等價與合併
# ============================================================
def _comp_match(a: ItemCandidate, b: ItemCandidate) -> bool:
    """component 比對：weapon 相容 + skin 相同 + stattrak 相同（wear 忽略，衝突由流程處理）。"""
    if a.weapon and b.weapon and a.weapon != b.weapon:
        return False
    if a.skin != b.skin:
        return False
    if a.stattrak != b.stattrak:
        return False
    return True


def _items_equivalent(a: ItemCandidate, b: ItemCandidate) -> bool:
    """等價鍵：mhn 相同 > weapon+skin+wear+stattrak > weapon+skin（wear 一方缺失）。"""
    if a.market_hash_name and b.market_hash_name:
        if a.market_hash_name.strip() == b.market_hash_name.strip():
            return True
        # mhn 不同（可能含不同 wear 寫法）→ 用 component 比對
        return _comp_match(a, b)
    return _comp_match(a, b)


def _find_equivalent(items: list[ItemCandidate], target: ItemCandidate) -> int | None:
    for i, it in enumerate(items):
        if _items_equivalent(it, target):
            return i
    return None


def _merge_items(
    text_items: list[ItemCandidate],
    dedup_evidence: list,
) -> tuple[list[ItemCandidate], list[EvidenceConflict], list[str], dict]:
    """text 優先合併。回傳 (merged_items, conflicts, warnings, image_item_map)。"""
    merged = copy.deepcopy(text_items)
    conflicts: list[EvidenceConflict] = []
    warnings: list[str] = []
    img_item_map: dict[tuple[int, int], int] = {}
    next_idx = len(merged)

    for ev_i, ev in enumerate(dedup_evidence):
        for ii, img_it in enumerate(ev.item_candidates):
            match = _find_equivalent(merged, img_it)
            if match is None:
                # B. 圖片新增商品
                merged.append(copy.deepcopy(img_it))
                img_item_map[(ev_i, ii)] = next_idx
                warnings.append(f"image_only_item:{next_idx}")
                next_idx += 1
                continue

            t = merged[match]

            # C. wear 衝突 → 兩筆保留
            if t.wear and img_it.wear and t.wear != img_it.wear:
                conflicts.append(EvidenceConflict(
                    conflict_type=ConflictType.WEAR_CONFLICT,
                    reason=f"wear 衝突: text={t.wear} image={img_it.wear}",
                    severity="warning",
                    text_item_index=match,
                    image_item_index=next_idx,
                    image_index=ev.image_index,
                ))
                merged.append(copy.deepcopy(img_it))
                img_item_map[(ev_i, ii)] = next_idx
                next_idx += 1
                continue

            # A. 相同商品 → 合併（text 為主、confidence max、補 wear 不降信心）
            if not t.wear and img_it.wear:
                t.wear = img_it.wear
            old_conf = t.confidence
            t.confidence = max(t.confidence, img_it.confidence)
            t.score = max(t.score, img_it.score)
            img_item_map[(ev_i, ii)] = match
            warnings.append(f"corroborated_by_image:{ev.image_index}")

            # E. role 衝突（不自動改寫 text role）
            if (t.role is not ItemRole.UNKNOWN and img_it.role is not ItemRole.UNKNOWN
                    and t.role is not img_it.role):
                conflicts.append(EvidenceConflict(
                    conflict_type=ConflictType.ROLE_CONFLICT,
                    reason=f"role 衝突: text={t.role.value} image={img_it.role.value}",
                    severity="warning",
                    text_item_index=match,
                    image_item_index=match,
                    image_index=ev.image_index,
                ))
            # D. mhn 衝突（等價但完整名不同）
            if (t.market_hash_name and img_it.market_hash_name
                    and t.market_hash_name != img_it.market_hash_name):
                conflicts.append(EvidenceConflict(
                    conflict_type=ConflictType.ITEM_NAME_CONFLICT,
                    reason=f"名稱衝突: text={t.market_hash_name} image={img_it.market_hash_name}",
                    severity="warning",
                    text_item_index=match,
                    image_index=ev.image_index,
                ))
            _ = old_conf
    return merged, conflicts, warnings, img_item_map


# ============================================================
# 圖片價格預關聯
# ============================================================
def _image_association(
    img_item_indexes: list[int],
    price_count: int,
    ev,
    conflicts: list[EvidenceConflict],
) -> list[int | None]:
    """回傳每個圖片 price 應關聯的 merged item index（None=不關聯）。"""
    if price_count == 0:
        return []
    # 1. 單 item + N prices → 全部綁該 item
    if len(img_item_indexes) == 1 and price_count >= 1:
        return [img_item_indexes[0]] * price_count
    # 2. item 數 == price 數 → 按順序（image_order_linking）
    if len(img_item_indexes) == price_count and price_count > 0:
        return list(img_item_indexes)
    # 3. 多 item 多 price 無明確關係 → AMBIGUOUS_LINK，不自動配對
    if img_item_indexes and price_count:
        conflicts.append(EvidenceConflict(
            conflict_type=ConflictType.AMBIGUOUS_LINK,
            reason=f"圖片 {ev.image_index} 多商品多價格無法確定對應",
            severity="error",
            image_index=ev.image_index,
        ))
    return [None] * price_count


# ============================================================
# Price 合併
# ============================================================
def _merge_prices(
    text_link_prices: list,
    dedup_evidence: list,
    img_item_map: dict,
    merged_items: list[ItemCandidate],
    conflicts: list[EvidenceConflict],
    warnings: list[str],
) -> list:
    """text prices 保留 + 圖片 prices 合併/新增。回傳 merged prices（associated 已設）。"""
    merged_prices = copy.deepcopy(text_link_prices)

    for ev_i, ev in enumerate(dedup_evidence):
        img_item_indexes = [
            img_item_map[(ev_i, ii)] for ii in range(len(ev.item_candidates))
            if (ev_i, ii) in img_item_map
        ]
        img_prices = ev.price_candidates
        assoc = _image_association(img_item_indexes, len(img_prices), ev, conflicts)
        if len(img_item_indexes) == len(img_prices) and len(img_prices) > 1:
            warnings.append(f"image_order_linking:{ev.image_index}")

        for pi, pc in enumerate(img_prices):
            target_item = assoc[pi] if pi < len(assoc) else None
            # 找等價 text price（同 item、同金額、同 currency、同 type）
            dup = _find_duplicate_price(merged_prices, pc, target_item)
            if dup is not None:
                merged_prices[dup].confidence = max(
                    merged_prices[dup].confidence, pc.confidence)
                warnings.append(f"corroborated_price_by_image:{ev.image_index}")
                continue

            # 衝突檢測（同 item 的 text price）
            if target_item is not None:
                for existing in merged_prices:
                    if existing.associated_item_index != target_item:
                        continue
                    if existing.money.amount != pc.money.amount:
                        # 同商品不同金額
                        if existing.price_type is PriceType.SELLER_ASK and pc.price_type is PriceType.SELLER_ASK:
                            conflicts.append(EvidenceConflict(
                                conflict_type=ConflictType.PRICE_CONFLICT,
                                reason=f"seller ask 衝突: text={existing.money.amount} image={pc.money.amount}",
                                severity="error",
                                text_price_index=merged_prices.index(existing),
                                image_price_index=len(merged_prices) + pi,
                                image_index=ev.image_index,
                            ))
                        continue
                    if existing.money.currency is not pc.money.currency:
                        conflicts.append(EvidenceConflict(
                            conflict_type=ConflictType.CURRENCY_CONFLICT,
                            reason=f"幣別衝突: text={existing.money.currency.value} image={pc.money.currency.value}",
                            severity="error",
                            text_price_index=merged_prices.index(existing),
                            image_price_index=len(merged_prices) + pi,
                            image_index=ev.image_index,
                        ))
                        continue

            # E. 圖片 UNKNOWN price → 保留但不覆蓋
            if pc.price_type is PriceType.UNKNOWN:
                warnings.append(f"image_unknown_price:{ev.image_index}")

            new_pc = copy.deepcopy(pc)
            new_pc.associated_item_index = target_item
            merged_prices.append(new_pc)
    return merged_prices


def _find_duplicate_price(merged_prices: list, pc, target_item: int | None) -> int | None:
    for i, existing in enumerate(merged_prices):
        if existing.associated_item_index != target_item:
            continue
        if (existing.money.amount == pc.money.amount
                and existing.money.currency is pc.money.currency
                and existing.price_type is pc.price_type):
            return i
    return None


# ============================================================
# 重建索引與 status
# ============================================================
def _rebuild_indexes(merged_items: list[ItemCandidate],
                     merged_prices: list) -> None:
    for it in merged_items:
        it.linked_price_indexes = []
    for pi, pc in enumerate(merged_prices):
        if pc.associated_item_index is not None:
            merged_items[pc.associated_item_index].linked_price_indexes.append(pi)
    for it in merged_items:
        it.linked_price_indexes = sorted(set(it.linked_price_indexes))


def _merged_status(
    original: ParseStatus,
    conflicts: list[EvidenceConflict],
    has_image_items: bool,
    has_image_only: bool,
    merged_items: list[ItemCandidate],
) -> ParseStatus:
    if original is ParseStatus.ERROR:
        return ParseStatus.ERROR
    # 任何 conflict（含 warning：wear/name/role）→ 至少 PARTIAL
    if conflicts:
        return ParseStatus.PARTIAL
    if not has_image_items:
        return original
    # 只有圖片 item（無文字對應）→ 需 Validation Gate，保守 PARTIAL
    if has_image_only:
        return ParseStatus.PARTIAL
    # 全部商品價格清楚 → OK
    if merged_items and all(it.linked_price_indexes for it in merged_items):
        return ParseStatus.OK
    return ParseStatus.PARTIAL


# ============================================================
# 主函式
# ============================================================
def merge_text_and_image_evidence(
    parsed_post: ParsedPost,
    image_evidence: list,
) -> MergedEvidenceResult:
    if not isinstance(parsed_post, ParsedPost):
        raise TypeError(f"parsed_post 必須是 ParsedPost，收到 {type(parsed_post).__name__}")
    if not isinstance(image_evidence, list):
        raise TypeError(f"image_evidence 必須是 list，收到 {type(image_evidence).__name__}")

    # ── 1. 圖片證據去重 ──
    dedup = deduplicate_image_evidence(image_evidence)
    dup_count = len(image_evidence) - len(dedup)

    text_items = copy.deepcopy(parsed_post.items)
    text_prices = copy.deepcopy(parsed_post.prices)

    # ── 2. Item 合併 ──
    merged_items, item_conflicts, item_warnings, img_item_map = _merge_items(
        text_items, dedup)

    # ── 3. Text 內部 linker（text prices 有文字位置）──
    text_link = link_prices_to_items(parsed_post.raw_text, merged_items, text_prices)
    merged_items = text_link.items
    merged_prices = text_link.prices

    # ── 4. Price 合併 + 圖片預關聯 ──
    conflicts = list(item_conflicts)
    warnings = list(item_warnings)
    merged_prices = _merge_prices(
        merged_prices, dedup, img_item_map, merged_items, conflicts, warnings)

    # ── 5. 重建索引（雙向一致）──
    _rebuild_indexes(merged_items, merged_prices)

    # ── 6. metadata / escalation ──
    image_candidate_count = sum(len(ev.item_candidates) for ev in dedup)
    metadata = dict(parsed_post.metadata)
    metadata.update({
        "image_evidence_count": len(dedup),
        "image_candidate_count": image_candidate_count,
        "merge_conflict_count": len(conflicts),
        "vision_merged": True,
    })

    has_image_items = image_candidate_count > 0
    has_image_only = any(w.startswith("image_only_item") for w in warnings)
    status = _merged_status(
        parsed_post.parse_status, conflicts, has_image_items, has_image_only,
        merged_items)

    escalation = None
    if any(c.severity == "error" for c in conflicts):
        escalation = "vision_merge_conflict"

    if dup_count:
        warnings.append(f"duplicate_images_removed:{dup_count}")

    # 去重保序
    warnings = list(dict.fromkeys(warnings))

    merged_post = ParsedPost(
        post_id=parsed_post.post_id,
        author=parsed_post.author,
        link=parsed_post.link,
        raw_text=parsed_post.raw_text,
        image_urls=list(parsed_post.image_urls),
        items=merged_items,
        prices=merged_prices,
        link_decisions=text_link.decisions,
        parse_status=status,
        intent=parsed_post.intent,
        warnings=list(parsed_post.warnings) + warnings,
        errors=list(parsed_post.errors),
        unlinked_item_indexes=[
            i for i, it in enumerate(merged_items) if not it.linked_price_indexes
        ],
        unlinked_price_indexes=[
            i for i, p in enumerate(merged_prices) if p.associated_item_index is None
        ],
        model_used=parsed_post.model_used,
        escalation_reason=escalation,
        source=parsed_post.source,
        metadata=metadata,
    )

    return MergedEvidenceResult(
        parsed_post=merged_post,
        image_evidence=[copy.deepcopy(ev) for ev in dedup],
        conflicts=conflicts,
        warnings=warnings,
        text_item_count=len(text_items),
        image_item_count=image_candidate_count,
        merged_item_count=len(merged_items),
        text_price_count=len(text_prices),
        image_price_count=sum(len(ev.price_candidates) for ev in dedup),
        merged_price_count=len(merged_prices),
    )
