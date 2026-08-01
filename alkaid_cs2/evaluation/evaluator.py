"""
evaluator.py — 單案例四 parser 評估（Phase 6.4A-6.4B.1）

legacy / text_v2 / vision_raw / vision_production 四組 prediction：
- vision_raw：raw Vision merge 本身（不經 production fallback）
- vision_production：fallback 後最終 production 結果
不呼叫真實 Facebook/BUFF/Vision API。
"""
import time
from decimal import Decimal
from typing import Callable

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.raw_post import RawPostInput
from alkaid_cs2.integration.production_bridge import (
    ProductionParseResult,
    parse_post_for_production,
)
from alkaid_cs2.integration.vision_production import (
    VisionImageInput,
    VisionMergeProductionResult,
    build_vision_merged_result,
)
from alkaid_cs2.evaluation.models import EvaluationCase
from alkaid_cs2.evaluation.prediction import EvaluationPrediction


def _items_from_post(post) -> list[dict]:
    """ParsedPost → 每 item 一筆（含完整價格關聯）。

    seller_price 只取 SELLER_ASK；price_indexes 保留原 ParsedPost price index。
    """
    out: list[dict] = []
    prices = list(post.prices or [])
    for item in post.items or []:
        seller_price = None
        currency = None
        price_idx = None
        for pi in item.linked_price_indexes or []:
            if pi < len(prices) and prices[pi].price_type.value == "seller_ask":
                seller_price = prices[pi].money.amount
                currency = prices[pi].money.currency
                price_idx = pi
                break
        out.append({
            "market_hash_name": item.market_hash_name,
            "wear": item.wear,
            "role": item.role.value if hasattr(item.role, "value") else str(item.role),
            "stattrak": item.stattrak,
            "seller_price": seller_price,
            "currency": currency,
            "price_idx": price_idx,
            "linked_price_indexes": list(item.linked_price_indexes or []),
        })
    return out


def _prediction_from_items(case: EvaluationCase, parser_name: str,
                           parsed_items: list[dict],
                           *, blocked: bool = False, source: str = "v2",
                           parse_status: str = "parsed",
                           conflicts: list[str] | None = None,
                           warnings: list[str] | None = None,
                           fallback_used: str | None = None,
                           image_count: int = 0,
                           vision_evidence_count: int = 0,
                           retry_count: int = 0,
                           latency_ms: float = 0.0) -> EvaluationPrediction:
    """從每 item 一筆的 dict 建立 prediction。

    跳過無 market_hash_name 的 item；所有 index（seller_price_item_indexes /
    item_to_price_pairs）使用**壓縮後** item index。
    未知/缺失 currency 保存 None（不默認 TWD）。
    """
    names: list[str] = []
    wears: list[str] = []
    roles: list[str] = []
    stattrak_values: list[bool | None] = []
    prices: list[Decimal] = []
    price_indexes: list[int] = []
    seller_item_indexes: list[int] = []
    currencies: list[Currency | None] = []
    price_types: list[str] = []
    item_to_price_pairs: list[tuple[int, int]] = []
    compressed: dict[int, int] = {}
    for i, it in enumerate(parsed_items):
        if not it.get("market_hash_name"):
            continue
        cidx = len(names)
        compressed[i] = cidx
        names.append(str(it["market_hash_name"]))
        wears.append(str(it.get("wear") or ""))
        roles.append(str(it.get("role") or ""))
        stattrak_values.append(it.get("stattrak"))
        if it.get("seller_price") is not None:
            prices.append(Decimal(str(it["seller_price"])))
            price_indexes.append(int(it["price_idx"]) if it.get("price_idx") is not None else cidx)
            seller_item_indexes.append(cidx)
            cur = it.get("currency")
            if isinstance(cur, Currency):
                currencies.append(cur)
            elif cur is not None:
                try:
                    currencies.append(Currency(str(cur).upper()))
                except Exception:
                    currencies.append(None)  # 未知 currency 不默認 TWD
            else:
                currencies.append(None)
            price_types.append("seller_ask")
    for i, it in enumerate(parsed_items):
        if i not in compressed:
            continue
        for pi in it.get("linked_price_indexes") or []:
            item_to_price_pairs.append((compressed[i], int(pi)))
    return EvaluationPrediction(
        case_id=case.case_id, parser_name=parser_name, source=source,
        blocked=blocked, parse_status=parse_status,
        market_hash_names=names, wear_values=wears, item_roles=roles,
        stattrak_values=stattrak_values,
        seller_prices=prices, seller_price_item_indexes=seller_item_indexes,
        currencies=currencies, price_indexes=price_indexes,
        price_types=price_types,
        item_to_price_pairs=item_to_price_pairs,
        conflicts=list(conflicts or []), warnings=list(warnings or []),
        fallback_used=fallback_used, latency_ms=latency_ms,
        image_count=image_count, vision_evidence_count=vision_evidence_count,
        retry_count=retry_count,
    )


def _prediction_from_production(case: EvaluationCase, parser_name: str,
                                result: ProductionParseResult,
                                parsed_items: list[dict] | None = None,
                                merged_items: list[dict] | None = None,
                                image_count: int = 0,
                                vision_evidence_count: int = 0,
                                retry_count: int = 0,
                                latency_ms: float = 0.0) -> EvaluationPrediction:
    """從 production 最終結果建立（vision_production / text_v2）。

    item 來源規則（Phase 6.4B.2）：
    - fallback_to_text → parsed_items（text items）
    - source == skipped → 空 items
    - Vision 成功未 fallback → merged_items（raw merge 的 items）
    """
    data = result.data or {}
    fallback = "none"
    if result.source == "skipped":
        fallback = "skipped"
    elif any(w.startswith("vision_fallback_to_text") for w in result.warnings):
        fallback = "text_v2"
    warnings = [str(w) for w in result.warnings]
    conflicts = [str(c) for c in result.warnings
                 if "conflict" in c or "ambiguous" in c]
    parse_status = ("blocked" if result.blocked else
                    ("skipped" if result.source == "skipped" else "parsed"))

    if result.source == "skipped":
        items_for_pred: list[dict] = []
    elif fallback == "text_v2" or parsed_items is None:
        items_for_pred = parsed_items or []
    else:
        items_for_pred = (merged_items if merged_items is not None
                          else parsed_items or [])
    if items_for_pred:
        return _prediction_from_items(
            case, parser_name, items_for_pred,
            blocked=result.blocked, source=result.source,
            parse_status=parse_status, conflicts=conflicts, warnings=warnings,
            fallback_used=fallback, image_count=image_count,
            vision_evidence_count=vision_evidence_count, retry_count=retry_count,
            latency_ms=latency_ms)
    # fallback：只有 selected item 的精簡輸出
    has_price = data.get("seller_price") is not None
    return EvaluationPrediction(
        case_id=case.case_id, parser_name=parser_name, source=result.source,
        blocked=result.blocked, parse_status=parse_status,
        market_hash_names=[str(data["market_hash_name"])]
        if data.get("market_hash_name") else [],
        seller_prices=[Decimal(str(data["seller_price"]))]
        if has_price else [],
        seller_price_item_indexes=[0] if has_price else [],
        currencies=[None] if has_price else [],  # 不假設 TWD
        price_types=["seller_ask"] if has_price else [],
        price_indexes=[0] if has_price else [],
        conflicts=conflicts, warnings=warnings, fallback_used=fallback,
        latency_ms=latency_ms, image_count=image_count,
        vision_evidence_count=vision_evidence_count, retry_count=retry_count,
    )


def _prediction_from_raw_merge(case: EvaluationCase,
                               raw: VisionMergeProductionResult,
                               latency_ms: float = 0.0) -> EvaluationPrediction:
    """從 raw Vision merge 建立（vision_raw：不經 production fallback）。"""
    parsed_items = _items_from_post(raw.merged_post) if raw.merged_post else []
    conflicts = [str(c.reason) for c in raw.conflicts]
    warnings = [str(w) for w in raw.warnings]
    blocked = bool(raw.blocked) or (raw.legacy_result is not None
                                    and bool(raw.legacy_result.blocked))
    return _prediction_from_items(
        case, "vision_raw", parsed_items,
        blocked=blocked, source="vision_raw",
        parse_status="blocked" if blocked else "parsed",
        conflicts=conflicts, warnings=warnings, fallback_used="none",
        image_count=len(case.images),
        vision_evidence_count=len(raw.image_evidence),
        latency_ms=latency_ms)


def evaluate_raw_vision_merge(case: EvaluationCase, *,
                              full_dict: dict[str, str],
                              pattern_dict: dict[str, str],
                              weapon_map: dict[str, str]) -> VisionMergeProductionResult:
    """評估 Vision merged 本身（不做 production fallback）。"""
    inputs = [
        VisionImageInput(image_index=im.image_index, image_url=im.image_url,
                         payload=im.vision_payload)
        for im in case.images
    ]
    post = RawPostInput(post_id=case.case_id, author=case.author, link=case.link,
                        raw_text=case.raw_text,
                        image_urls=[im.image_url for im in case.images],
                        source="facebook")
    return build_vision_merged_result(
        post, vision_inputs=inputs,
        full_dict=full_dict, pattern_dict=pattern_dict, weapon_map=weapon_map,
    )


def _parse_text_post(case: EvaluationCase, *, full_dict, pattern_dict,
                     weapon_map):
    from alkaid_cs2.pipeline.parse_pipeline import parse_post
    return parse_post(
        RawPostInput(post_id=case.case_id, author=case.author, link=case.link,
                     raw_text=case.raw_text,
                     image_urls=[im.image_url for im in case.images],
                     source="facebook"),
        full_dict=full_dict, pattern_dict=pattern_dict, weapon_map=weapon_map)


def evaluate_case(case: EvaluationCase, *,
                  full_dict: dict[str, str],
                  pattern_dict: dict[str, str],
                  weapon_map: dict[str, str],
                  legacy_parser: Callable[[str], dict | None],
                  ) -> dict[str, object]:
    """產生 legacy / text_v2 / vision_raw / vision_production 四組 prediction。

    相容 alias：vision_v2 → vision_production（向後相容）。
    """
    results: dict[str, object] = {}

    # ── A. legacy ──
    started = time.perf_counter()
    raw = legacy_parser(case.raw_text)
    latency = (time.perf_counter() - started) * 1000.0
    legacy_pred = _prediction_from_legacy_dict(case.case_id, raw)
    legacy_pred.latency_ms = round(latency, 1)
    results["legacy"] = legacy_pred

    # ── text post（text_v2 / vision_production fallback 共用）──
    text_post = _parse_text_post(case, full_dict=full_dict,
                                 pattern_dict=pattern_dict, weapon_map=weapon_map)
    text_items = _items_from_post(text_post)

    # ── B. text_v2（v2_only、無 vision）──
    started = time.perf_counter()
    text_result = parse_post_for_production(
        post_id=case.case_id, author=case.author, link=case.link,
        post_text=case.raw_text, image_urls=[im.image_url for im in case.images],
        vision_inputs=None,
        full_dict=full_dict, pattern_dict=pattern_dict, weapon_map=weapon_map,
        legacy_parser=legacy_parser, mode="v2_only",
    )
    latency = (time.perf_counter() - started) * 1000.0
    results["text_v2"] = _prediction_from_production(
        case, "text_v2", text_result, parsed_items=text_items,
        image_count=len(case.images), latency_ms=round(latency, 1))

    # ── C. vision_raw（raw merge 本身，不經 fallback）──
    started = time.perf_counter()
    try:
        raw_merge = evaluate_raw_vision_merge(
            case, full_dict=full_dict, pattern_dict=pattern_dict,
            weapon_map=weapon_map)
    except (ValueError, TypeError, KeyError) as exc:
        raw_merge = None
        results["raw_vision_merge_error"] = f"{type(exc).__name__}:{str(exc)[:200]}"
    latency = (time.perf_counter() - started) * 1000.0
    if raw_merge is not None:
        results["vision_raw"] = _prediction_from_raw_merge(
            case, raw_merge, latency_ms=round(latency, 1))
        results["raw_vision_merge"] = raw_merge
    else:
        results["vision_raw"] = EvaluationPrediction(
            case_id=case.case_id, parser_name="vision_raw", source="error",
            blocked=True, parse_status="error", fallback_used="skipped",
            latency_ms=round(latency, 1))

    # ── D. vision_production（fallback 後最終 production 結果）──
    inputs = [
        VisionImageInput(image_index=im.image_index, image_url=im.image_url,
                         payload=im.vision_payload)
        for im in case.images
    ]
    started = time.perf_counter()
    vision_result = parse_post_for_production(
        post_id=case.case_id, author=case.author, link=case.link,
        post_text=case.raw_text, image_urls=[im.image_url for im in case.images],
        vision_inputs=inputs,
        full_dict=full_dict, pattern_dict=pattern_dict, weapon_map=weapon_map,
        legacy_parser=legacy_parser, mode="v2_only",
    )
    latency = (time.perf_counter() - started) * 1000.0
    vision_summary = vision_result.vision_summary or {}
    # item 來源（Phase 6.4B.2）：fallback→text_items；skipped→空；
    # 無 fallback→raw merge merged_post 的 items（不得用 text_post）
    merged_items = None
    if raw_merge is not None and raw_merge.merged_post is not None:
        merged_items = _items_from_post(raw_merge.merged_post)
    results["vision_production"] = _prediction_from_production(
        case, "vision_production", vision_result,
        parsed_items=text_items, merged_items=merged_items,
        image_count=len(case.images),
        vision_evidence_count=int(vision_summary.get("evidence_count", 0)
                                  or vision_summary.get("vision_evidence_count", 0)),
        retry_count=int(vision_summary.get("retry_count", 0)),
        latency_ms=round(latency, 1))
    # 向後相容 alias
    results["vision_v2"] = results["vision_production"]

    return results


def _prediction_from_legacy_dict(case_id: str, raw: dict | None) -> EvaluationPrediction:
    """legacy_parser 結果（dict|None）→ EvaluationPrediction。"""
    if raw is None:
        return EvaluationPrediction(
            case_id=case_id, parser_name="legacy", source="legacy",
            blocked=True, parse_status="no_result", fallback_used=None,
        )
    mhn = raw.get("market_hash_name") or raw.get("name")
    price = raw.get("seller_price", raw.get("price"))
    cur = raw.get("currency")
    prices: list[Decimal] = []
    price_indexes: list[int] = []
    currencies: list[Currency | None] = []
    names: list[str] = []
    if price is not None:
        try:
            prices.append(Decimal(str(price)))
            price_indexes.append(0)
            if cur is not None:
                try:
                    currencies.append(Currency(str(cur).upper()))
                except Exception:
                    currencies.append(None)  # 未知 currency 不默認
            else:
                currencies.append(None)
        except Exception:
            pass
    if mhn:
        names.append(str(mhn))
    return EvaluationPrediction(
        case_id=case_id, parser_name="legacy", source="legacy",
        blocked=bool(raw.get("blocked", False)),
        parse_status="blocked" if raw.get("blocked") else "parsed",
        market_hash_names=names, seller_prices=prices,
        seller_price_item_indexes=price_indexes, currencies=currencies,
        price_indexes=price_indexes,
        price_types=["seller_ask"] if prices else [],
        item_roles=[str(raw["role"])] if raw.get("role") else [],
        wear_values=[str(raw["wear"])] if raw.get("wear") else [],
        warnings=[str(w) for w in raw.get("warnings", []) if isinstance(w, str)],
        fallback_used=None,
    )
