"""
vision_adapter.py — Vision payload → V2 領域模型（V2 Phase 6.3A）

流程：
  VisionRawResult（標準化中介層）
  → normalize_vision_payload(payload)
  → vision_result_to_evidence(result)
  → ImageEvidence（含 ItemCandidate / PriceCandidate）

安全規則：
- 不得自動相信 market_hash_name（不標 verified）
- 不得執行貨幣換算
- price_type 不確定 → UNKNOWN
- inventory grid / payment proof 不建立交易候選
- market listing 價格不得自動視為 seller ask
"""
import json
import copy
from decimal import Decimal, InvalidOperation

from alkaid_cs2.domain.enums import Currency
from alkaid_cs2.domain.image_evidence import (
    ImageEvidence,
    ImageEvidenceSource,
    ImageKind,
    ImagePlatform,
)
from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole
from alkaid_cs2.domain.price import Money
from alkaid_cs2.domain.price_candidate import PriceCandidate, PriceSource, PriceType
from alkaid_cs2.domain.vision_result import VisionRawItem, VisionRawResult
from alkaid_cs2.parsers.item_parser import STAR_WEAPONS

# 磨損中文 → 英文（與 item_parser 同對照，含簡繁相反陷阱）
_WEAR_FROM_STR = [
    (("嶄新出廠", "崭新出厂", "厂新", "全新", "嶄新", "崭新"), "Factory New"),
    (("輕微磨損", "略有磨损", "略有磨損"), "Minimal Wear"),
    (("久經沙場", "久经沙场", "久經", "久经"), "Field-Tested"),
    (("战痕累累", "破損不堪"), "Battle-Scarred"),
    (("破损不堪", "戰痕累累"), "Well-Worn"),
]

_SELL_KEYWORDS = ("售", "賣", "算", "開價", "帶走")
_BUFF_FLOOR_KEYWORDS = ("同磨底", "BUFF底", "buff底")
_BUNDLE_KEYWORDS = ("兩把一起", "兩件打包", "打包", "全收", "兩把", "一起")


# ============================================================
# 小工具
# ============================================================
def _wear_from_str(s: str | None) -> str | None:
    if not s:
        return None
    for keywords, wear_en in _WEAR_FROM_STR:
        for kw in keywords:
            if kw in s:
                return wear_en
    return None


def _to_decimal(v) -> Decimal | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError):
        return None
    return d


def _parse_currency(v) -> tuple[Currency, bool]:
    """回傳 (Currency, unknown_warning)。None/空 = UNKNOWN 無 warning；無法辨識字串 = UNKNOWN + warning。"""
    if v is None:
        return Currency.UNKNOWN, False
    s = str(v).strip().upper()
    if s in ("RMB", "CNY", "人民幣", "¥"):
        return Currency.RMB, False
    if s in ("TWD", "NT", "NT$", "台幣", "新台幣"):
        return Currency.TWD, False
    if s in ("USD", "US", "美元", "$"):
        return Currency.USD, False
    if s in ("", "UNKNOWN", "NONE", "?"):
        return Currency.UNKNOWN, False
    return Currency.UNKNOWN, True


def _coerce_conf(v, kind: ImageKind) -> tuple[float, bool]:
    """回傳 (confidence, invalid)。None=未提供用基礎值；非法值=0.0 + invalid=True。"""
    import math

    if v is None:
        # 未提供：SINGLE/MULTI 基礎 0.70，其他 0.50
        base = 0.70 if kind in (ImageKind.SINGLE_ITEM, ImageKind.MULTI_ITEM) else 0.50
        return base, False
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return 0.0, True
    if not math.isfinite(v):
        return 0.0, True
    if not (0.0 <= v <= 1.0):
        return 0.0, True
    c = float(v)
    # 合法 0.0 不得提升
    if kind is ImageKind.UNKNOWN:
        c = min(c, 0.50)
    return c, False


def _parse_bbox(v) -> tuple[int, int, int, int] | None:
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    try:
        vals = tuple(int(x) for x in v)
    except (TypeError, ValueError):
        return None
    if any(x < 0 for x in vals):
        return None
    return vals  # type: ignore[return-value]


def _clean_mhn(v) -> str | None:
    if not isinstance(v, str):
        return None
    s = v.strip()
    if s in ("", "UNKNOWN", "NONE", "unknown", "none"):
        return None
    return s


# ============================================================
# 類型 / 平台 / 價格型別 / role 推斷
# ============================================================
def _classify_kind(payload: dict, raw_items: list) -> ImageKind:
    t = str(payload.get("type") or payload.get("image_kind") or payload.get("image_type") or "").strip().lower()
    mapping = {
        "inventory": ImageKind.INVENTORY_GRID,
        "inventory_grid": ImageKind.INVENTORY_GRID,
        "single": ImageKind.SINGLE_ITEM,
        "single_item": ImageKind.SINGLE_ITEM,
        "multi": ImageKind.MULTI_ITEM,
        "multi_item": ImageKind.MULTI_ITEM,
        "market": ImageKind.MARKET_LISTING,
        "market_listing": ImageKind.MARKET_LISTING,
        "buff_listing": ImageKind.MARKET_LISTING,
        "steam_listing": ImageKind.MARKET_LISTING,
        "steam": ImageKind.MARKET_LISTING,
        "chat": ImageKind.CHAT_SCREENSHOT,
        "chat_screenshot": ImageKind.CHAT_SCREENSHOT,
        "inspect": ImageKind.INSPECT_SCREENSHOT,
        "inspect_screenshot": ImageKind.INSPECT_SCREENSHOT,
        "payment": ImageKind.PAYMENT_PROOF,
        "payment_proof": ImageKind.PAYMENT_PROOF,
        "trade": ImageKind.TRADE_CONFIRMATION,
        "trade_confirmation": ImageKind.TRADE_CONFIRMATION,
    }
    if t in mapping:
        return mapping[t]
    if t:
        return ImageKind.UNKNOWN
    if len(raw_items) > 1:
        return ImageKind.MULTI_ITEM
    if len(raw_items) == 1:
        return ImageKind.SINGLE_ITEM
    return ImageKind.UNKNOWN


def _infer_platform_from_payload(payload: dict, kind: ImageKind) -> ImagePlatform:
    p = str(payload.get("platform") or payload.get("source_platform") or "").lower()
    if "steam" in p:
        return ImagePlatform.STEAM
    if "buff" in p:
        return ImagePlatform.BUFF163
    if "fb" in p or "facebook" in p:
        return ImagePlatform.FACEBOOK
    if kind is ImageKind.MARKET_LISTING:
        text = json.dumps(payload, ensure_ascii=False).lower()
        if "steam" in text:
            return ImagePlatform.STEAM
        if "buff" in text:
            return ImagePlatform.BUFF163
    return ImagePlatform.UNKNOWN


def _infer_price_type(raw: dict, kind: ImageKind) -> PriceType:
    text = str(raw.get("evidence") or raw.get("raw_name") or raw.get("chinese_name") or "")
    # MARKET_LISTING 語意優先：掛牌價 ≠ 賣家售價（即使文字含「售價」）
    if kind is ImageKind.MARKET_LISTING:
        if any(k in text for k in (*_BUFF_FLOOR_KEYWORDS, "最低價")):
            return PriceType.BUFF_FLOOR  # BUFF 平台底價
        return PriceType.REFERENCE
    if any(k in text for k in _SELL_KEYWORDS):
        return PriceType.SELLER_ASK
    if any(k in text for k in _BUFF_FLOOR_KEYWORDS):
        return PriceType.BUFF_FLOOR
    if "=" in text and ("*" in text or "×" in text):
        return PriceType.CALCULATED
    if any(k in text for k in _BUNDLE_KEYWORDS):
        return PriceType.BUNDLE_TOTAL
    if kind is ImageKind.INSPECT_SCREENSHOT:
        return PriceType.UNKNOWN
    return PriceType.UNKNOWN


def _infer_role(raw: dict, kind: ImageKind) -> ItemRole:
    text = str(raw.get("evidence") or raw.get("raw_name") or raw.get("chinese_name") or "")
    if any(k in text for k in ("收", "徵", "求購")):
        return ItemRole.BUYING
    if any(k in text for k in ("換", "交換", "貼換")):
        return ItemRole.TRADE
    if any(k in text for k in _SELL_KEYWORDS):
        return ItemRole.SELLING
    if kind is ImageKind.MARKET_LISTING:
        return ItemRole.REFERENCE
    if kind in (ImageKind.INSPECT_SCREENSHOT, ImageKind.INVENTORY_GRID):
        return ItemRole.SHOWCASE
    return ItemRole.UNKNOWN


# ============================================================
# normalize_vision_payload
# ============================================================
def normalize_vision_payload(payload: object, *, image_index: int) -> VisionRawResult:
    warnings: list[str] = []
    errors: list[str] = []

    # 1. None
    if payload is None:
        errors.append("payload_is_none")
        return VisionRawResult(image_index, ImageKind.UNKNOWN, ImagePlatform.UNKNOWN,
                               [], 0.0, {}, warnings, errors)

    # 2. JSON string / code fence
    if isinstance(payload, str):
        text = payload.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            payload = json.loads(text)
        except Exception:
            errors.append("invalid_json")
            return VisionRawResult(image_index, ImageKind.UNKNOWN, ImagePlatform.UNKNOWN,
                                   [], 0.0, {}, warnings, errors)

    # 3. dict / list
    if isinstance(payload, dict):
        raw_payload = copy.deepcopy(payload)
        items_raw = payload.get("items")
        if isinstance(items_raw, list):
            raw_items = items_raw
        elif items_raw is None:
            raw_items = [payload]  # 單一商品 dict
        else:
            raw_items = []
            warnings.append("items_not_list")
    elif isinstance(payload, list):
        raw_payload = {"items": copy.deepcopy(payload)}
        raw_items = payload
    else:
        errors.append(f"invalid_payload_type:{type(payload).__name__}")
        return VisionRawResult(image_index, ImageKind.UNKNOWN, ImagePlatform.UNKNOWN,
                               [], 0.0, {}, warnings, errors)

    kind = _classify_kind(raw_payload, raw_items)
    platform = _infer_platform_from_payload(raw_payload, kind)

    # 4. 逐筆解析（一筆錯誤不影響整張圖）
    items: list[VisionRawItem] = []
    for i, raw in enumerate(raw_items):
        if not isinstance(raw, dict):
            warnings.append(f"item[{i}]_not_dict_skipped")
            continue
        item = _parse_raw_item(raw, i, kind, warnings)
        if item is not None:
            items.append(item)

    overall = _overall_confidence(items)
    return VisionRawResult(image_index, kind, platform, items, overall,
                           raw_payload, warnings, errors)


def _overall_confidence(items: list[VisionRawItem]) -> float:
    if not items:
        return 0.0
    return sum(it.confidence for it in items) / len(items)


def _parse_raw_item(raw: dict, idx: int, kind: ImageKind,
                    warnings: list[str]) -> VisionRawItem | None:
    try:
        mhn = _clean_mhn(raw.get("market_hash_name")
                         or raw.get("en_name") or raw.get("english_name"))
        raw_name = raw.get("chinese_name") or raw.get("name") or raw.get("raw_name")
        weapon = raw.get("weapon") or raw.get("weapon_name")
        skin = (raw.get("skin") or raw.get("pattern") or raw.get("skin_name")
                or raw_name)  # chinese_name fallback → skin-only candidate
        wear = raw.get("wear") or raw.get("wear_en")
        stattrak = raw.get("stattrak")
        if isinstance(stattrak, str):
            stattrak = stattrak.strip().lower() in ("true", "yes", "1", "是")
        price = raw.get("price")
        if price is None:
            price = raw.get("seller_price")
        price_amount = _to_decimal(price)
        currency, cur_warn = _parse_currency(raw.get("currency"))
        if cur_warn:
            warnings.append("unknown_currency")
        price_type = _infer_price_type(raw, kind)
        role = _infer_role(raw, kind)
        # 單筆 platform（item 層）
        platform = ImagePlatform.UNKNOWN
        p_str = str(raw.get("platform") or raw.get("source_platform") or "").lower()
        if "steam" in p_str:
            platform = ImagePlatform.STEAM
        elif "buff" in p_str:
            platform = ImagePlatform.BUFF163
        confidence, conf_invalid = _coerce_conf(raw.get("confidence"), kind)
        if conf_invalid:
            warnings.append("invalid_confidence")
        evidence_text = str(raw.get("evidence") or raw_name or "")
        bbox = _parse_bbox(raw.get("bbox"))
        if confidence < 0.50:
            warnings.append("low_confidence")
        return VisionRawItem(
            raw_name=raw_name if isinstance(raw_name, str) else None,
            market_hash_name=mhn,
            weapon=weapon if isinstance(weapon, str) else None,
            skin=skin if isinstance(skin, str) else None,
            wear=wear if isinstance(wear, str) else None,
            stattrak=stattrak,
            price_amount=price_amount,
            currency=currency,
            price_type=price_type,
            role=role,
            platform=platform,
            confidence=confidence,
            evidence_text=evidence_text,
            bbox=bbox,
            warnings=[],
        )
    except Exception as exc:
        warnings.append(f"item[{idx}]_parse_error:{exc}")
        return None


# ============================================================
# vision_result_to_evidence
# ============================================================
def _assemble_mhn(weapon: str | None, skin: str | None, wear: str | None,
                  stattrak: bool | None) -> str | None:
    if skin is None and weapon is None:
        return None
    core = f"{weapon} | {skin}" if weapon else skin or ""
    if not core.strip():
        return None
    prefix = ""
    if weapon and weapon in STAR_WEAPONS:
        prefix += "★ "
    if stattrak:
        prefix += "StatTrak™ "
    if wear:
        core = f"{core} ({wear})"
    return f"{prefix}{core}"


def vision_result_to_evidence(
    result: VisionRawResult,
    *,
    image_url: str,
    image_hash: str | None = None,
) -> ImageEvidence:
    if not isinstance(result, VisionRawResult):
        raise TypeError(f"result 必須是 VisionRawResult，收到 {type(result).__name__}")

    item_candidates: list[ItemCandidate] = []
    price_candidates: list[PriceCandidate] = []
    warnings = list(result.warnings)
    item_warnings: list[str] = []

    # 非交易圖片：一律不得建立 PriceCandidate（即使 payload 錯誤提供 price/售語境）
    NO_PRICE_KINDS = (
        ImageKind.INVENTORY_GRID,
        ImageKind.INSPECT_SCREENSHOT,
        ImageKind.PAYMENT_PROOF,
        ImageKind.TRADE_CONFIRMATION,
    )

    for raw in result.items:
        # INVENTORY_GRID：不建立任何候選
        if result.image_kind is ImageKind.INVENTORY_GRID:
            warnings.append("inventory_grid_deferred")
            continue
        # PAYMENT_PROOF：不建立候選
        if result.image_kind is ImageKind.PAYMENT_PROOF:
            continue

        # ── ItemCandidate ──
        ic = _to_item_candidate(raw, result, item_warnings)
        if ic is not None:
            item_candidates.append(ic)

        # ── PriceCandidate（非交易圖片一律不建）──
        if result.image_kind in NO_PRICE_KINDS:
            continue
        pc = _to_price_candidate(raw, result, item_warnings)
        if pc is not None:
            price_candidates.append(pc)

    warnings.extend(item_warnings)
    return ImageEvidence(
        image_index=result.image_index,
        image_url=image_url,
        image_hash=image_hash,
        image_kind=result.image_kind,
        platform=result.platform,
        source=ImageEvidenceSource.VISION,
        raw_result=result.raw_payload,
        item_candidates=item_candidates,
        price_candidates=price_candidates,
        confidence=result.overall_confidence,
        warnings=warnings,
        errors=list(result.errors),
    )


def _to_item_candidate(raw: VisionRawItem, result: VisionRawResult,
                       warnings: list[str]) -> ItemCandidate | None:
    if raw.market_hash_name is None and raw.skin is None and raw.weapon is None:
        warnings.append("item_no_name_skipped")
        return None

    wear_en = _wear_from_str(raw.wear) if raw.wear else None
    conf = raw.confidence

    # 缺 weapon → skin-only，confidence 上限 0.60
    if raw.weapon is None:
        conf = min(conf, 0.60)

    # 名稱結構衝突：完整名與 weapon/skin 不一致
    mhn = raw.market_hash_name
    if mhn and raw.weapon and raw.skin:
        if raw.weapon not in mhn or raw.skin not in mhn:
            warnings.append("name_component_conflict")
            conf = min(conf, 0.50)

    if conf < 0.50:
        warnings.append("low_confidence")

    if mhn is None:
        mhn = _assemble_mhn(raw.weapon, raw.skin, wear_en, raw.stattrak)

    original_text = raw.evidence_text or raw.raw_name or f"vision:{raw.skin or raw.market_hash_name or 'unknown'}"
    return ItemCandidate(
        market_hash_name=mhn,
        weapon=raw.weapon,
        skin=raw.skin,
        wear=wear_en,
        stattrak=bool(raw.stattrak) if raw.stattrak is not None else False,
        role=raw.role,
        original_text=original_text,
        matched_text=raw.evidence_text or None,
        parser="vision_adapter",
        evidence=ItemEvidence.VISION,
        confidence=conf,
        score=round(conf * 100.0, 2),
        verified=False,
        image_index=result.image_index,
    )


def _to_price_candidate(raw: VisionRawItem, result: VisionRawResult,
                        warnings: list[str]) -> PriceCandidate | None:
    if raw.price_amount is None:
        return None
    if raw.price_amount <= 0:
        warnings.append("price_non_positive_skipped")
        return None
    if raw.price_amount < Decimal("50") or raw.price_amount > Decimal("5000000"):
        warnings.append("suspicious_price_range")

    price_type = raw.price_type
    # INSPECT_SCREENSHOT 不產 seller price
    if result.image_kind is ImageKind.INSPECT_SCREENSHOT and price_type is PriceType.SELLER_ASK:
        price_type = PriceType.UNKNOWN
        warnings.append("inspect_price_not_seller_ask")

    # source 依圖片類型
    if result.image_kind is ImageKind.CHAT_SCREENSHOT:
        source = PriceSource.CHAT
    elif result.image_kind is ImageKind.MARKET_LISTING:
        source = PriceSource.MARKET_SCREENSHOT
    else:
        source = PriceSource.IMAGE

    evidence = raw.evidence_text or str(raw.price_amount)
    return PriceCandidate(
        money=Money(raw.price_amount, raw.currency),
        price_type=price_type,
        source=source,
        evidence=evidence,
        confidence=raw.confidence,
        image_index=result.image_index,
    )


# ============================================================
# 一鍵轉換
# ============================================================
def vision_payload_to_evidence(
    payload: object,
    *,
    image_index: int,
    image_url: str,
    image_hash: str | None = None,
) -> ImageEvidence:
    """payload → normalize → evidence（一鍵）。"""
    result = normalize_vision_payload(payload, image_index=image_index)
    return vision_result_to_evidence(result, image_url=image_url, image_hash=image_hash)
