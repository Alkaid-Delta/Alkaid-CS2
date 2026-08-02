"""
item_parser.py — deterministic 商品候選解析器（V2 Phase 3.1）

只做 dictionary parsing，不呼叫任何模型或外部 API。

Phase 3.1 改善：
- 每個候選先計算自己的 local segment（item_spans + 標點 + max_window）
- detect_role / find_weapon / detect_wear / detect_stattrak 都在 segment 內判斷
- 避免固定視窗造成的跨商品誤判
"""
import re

from alkaid_cs2.domain.item_candidate import ItemCandidate, ItemEvidence, ItemRole

# Phase P2.1：模組級 canonical validator（延遲初始化）
_PARSER_VALIDATOR = None
def _get_parser_validator():
    global _PARSER_VALIDATOR
    if _PARSER_VALIDATOR is None:
        from alkaid_cs2.services.item_validator import ItemValidator
        _PARSER_VALIDATOR = ItemValidator()
    return _PARSER_VALIDATOR

# ── 刀/手套類（需要 ★ 前綴）──
STAR_WEAPONS = {
    "Butterfly Knife", "Karambit", "Bayonet", "Flip Knife", "Huntsman Knife",
    "Gut Knife", "Sport Gloves", "Specialist Gloves", "Driver Gloves",
    "Moto Gloves", "Hand Wraps", "Bloodhound Gloves", "Ursus Knife",
    "Talon Knife", "Stiletto Knife", "Survival Knife", "Nomad Knife",
    "Skeleton Knife", "Paracord Knife", "Classic Knife", "Kukri Knife",
}

# ── 角色關鍵詞 → 角色映射 ──
ROLE_OF = {
    "售": ItemRole.SELLING, "賣": ItemRole.SELLING, "出": ItemRole.SELLING,
    "開價": ItemRole.SELLING, "帶走": ItemRole.SELLING, "算": ItemRole.SELLING,
    "算你": ItemRole.SELLING,
    "收": ItemRole.BUYING, "徵": ItemRole.BUYING, "求購": ItemRole.BUYING,
    "換": ItemRole.TRADE, "交換": ItemRole.TRADE, "貼換": ItemRole.TRADE,
    "參考": ItemRole.REFERENCE, "同磨底": ItemRole.REFERENCE,
    "BUFF底": ItemRole.REFERENCE, "buff底": ItemRole.REFERENCE,
    "對照": ItemRole.REFERENCE,
}

# 候選前方可用的角色詞（全部）
_FRONT_KEYWORDS = tuple(ROLE_OF.keys())
# 候選後方只可用：帶走/算/算你/同磨底/參考/換（trade 雙向）
_BACK_KEYWORDS = ("帶走", "算", "算你", "同磨底", "參考", "BUFF底", "對照",
                  "換", "交換", "貼換")

# tie-break 優先級（相同距離時）
_ROLE_PRIORITY = (
    ItemRole.TRADE, ItemRole.BUYING, ItemRole.SELLING,
    ItemRole.REFERENCE, ItemRole.UNKNOWN,
)

# ── 磨損對照（簡繁相反陷阱，與 analyze_arbitrage._wear_to_en 同邏輯）──
_WEAR_TABLE = [
    (("嶄新出廠", "崭新出厂", "厂新", "全新", "嶄新", "崭新"), "Factory New"),
    (("輕微磨損", "略有磨损", "略有磨損"), "Minimal Wear"),
    (("久經沙場", "久经沙场", "久經", "久经"), "Field-Tested"),
    (("战痕累累", "破損不堪"), "Battle-Scarred"),   # 簡体战痕累累=BS / 繁體破損不堪=BS
    (("破损不堪", "戰痕累累"), "Well-Worn"),          # 簡体破损不堪=WW / 繁體戰痕累累=WW
    (("戰痕", "战痕"), "Battle-Scarred"),
    (("破損", "破损"), "Well-Worn"),
]

_STAT_TRAK_KEYWORDS = ("暗金", "StatTrak", "StatTrak™")

_SEGMENT_PUNCTS = ("。", "！", "？", "\n", "；", ";", "|")


# ============================================================
# 區段計算
# ============================================================
def find_candidate_segment(
    text: str,
    start: int,
    end: int,
    item_spans: list[tuple[int, int]] | None = None,
    max_window: int = 40,
) -> tuple[int, int]:
    """
    計算候選的 local segment。

    1. item_spans：左邊界 = 前一個商品結束；右邊界 = 下一個商品開始
    2. 標點縮小：。！？\\n；;|
    3. max_window 限制
    4. 保證包含候選 start:end
    """
    seg_start, seg_end = 0, len(text)

    # 1. item_spans 邊界
    if item_spans:
        for s, e in sorted(item_spans):
            if e <= start and e > seg_start:
                seg_start = e
            elif s >= end:
                seg_end = s
                break

    # 2. 標點縮小（在 item_spans 邊界內找最近的標點）
    best = seg_start
    for punct in _SEGMENT_PUNCTS:
        idx = text.rfind(punct, seg_start, start)
        if idx != -1 and idx + 1 > best:
            best = idx + 1
    seg_start = best

    best = seg_end
    for punct in _SEGMENT_PUNCTS:
        idx = text.find(punct, end, seg_end)
        if idx != -1 and idx < best:
            best = idx
    seg_end = best

    # 3. max_window
    if start - seg_start > max_window:
        seg_start = start - max_window
    if seg_end - end > max_window:
        seg_end = end + max_window

    # 4. 保證包含候選
    seg_start = min(seg_start, start)
    seg_end = max(seg_end, end)
    return seg_start, seg_end


# ============================================================
# 角色判斷（segment 內）
# ============================================================
def detect_role(
    text: str,
    start: int,
    end: int,
    *,
    segment_start: int | None = None,
    segment_end: int | None = None,
) -> ItemRole:
    """只在候選 segment 內判斷角色。前方交易詞優先；後方只認少數詞。"""
    if segment_start is None:
        segment_start = max(0, start - 25)
    if segment_end is None:
        segment_end = min(len(text), end + 25)

    front = text[segment_start:start]
    back = text[end:segment_end]

    hits: list[tuple[int, ItemRole]] = []

    # 候選前方：全部角色詞
    for kw in _FRONT_KEYWORDS:
        pat = re.compile(re.escape(kw))
        for m in pat.finditer(front):
            hits.append((start - (segment_start + m.start()), ROLE_OF[kw]))

    # 候選後方：只認少數詞
    for kw in _BACK_KEYWORDS:
        pat = re.compile(re.escape(kw))
        for m in pat.finditer(back):
            hits.append((m.start(), ROLE_OF[kw]))

    if not hits:
        return ItemRole.UNKNOWN

    # 最近距離優先；相同距離 tie-break
    min_dist = min(d for d, _ in hits)
    tied_roles = {r for d, r in hits if d == min_dist}
    for role in _ROLE_PRIORITY:
        if role in tied_roles:
            return role
    return ItemRole.UNKNOWN


# ============================================================
# 武器配對（segment 內，前方優先）
# ============================================================
def find_weapon(
    text: str,
    start: int,
    end: int,
    weapon_map: dict[str, str],
    *,
    segment_start: int | None = None,
    segment_end: int | None = None,
) -> tuple[str | None, int | None]:
    """只在候選 segment 內找武器。前方優先、同方向取最近。無法判定回 None。"""
    if segment_start is None:
        segment_start = max(0, start - 15)
    if segment_end is None:
        segment_end = min(len(text), end + 15)

    front = text[segment_start:start]
    back = text[end:segment_end]

    best_front: tuple[int, str] | None = None  # (pos, weapon)
    for cn_w, en_w in weapon_map.items():
        if len(cn_w) < 2:
            continue
        idx = front.rfind(cn_w)
        if idx != -1:
            pos = segment_start + idx
            if best_front is None or pos > best_front[0]:
                best_front = (pos, en_w)

    best_back: tuple[int, str] | None = None
    for cn_w, en_w in weapon_map.items():
        if len(cn_w) < 2:
            continue
        idx = back.find(cn_w)
        if idx != -1:
            pos = end + idx
            if best_back is None or pos < best_back[0]:
                best_back = (pos, en_w)

    # 前方優先；無前方才用後方
    if best_front:
        return best_front[1], best_front[0]
    if best_back:
        return best_back[1], best_back[0]
    return None, None


# ============================================================
# 磨損 / StatTrak（segment 內）
# ============================================================
def detect_wear(
    text: str,
    start: int,
    end: int,
    *,
    segment_start: int | None = None,
    segment_end: int | None = None,
) -> str | None:
    """segment 內找磨損詞。沒有磨損回 None（不得預設 Field-Tested）。"""
    if segment_start is None:
        segment_start = max(0, start - 12)
    if segment_end is None:
        segment_end = min(len(text), end + 12)
    seg = text[segment_start:segment_end]
    for keywords, wear_en in _WEAR_TABLE:
        for kw in keywords:
            if kw in seg:
                return wear_en
    return None


def detect_stattrak(
    text: str,
    start: int,
    end: int,
    *,
    segment_start: int | None = None,
    segment_end: int | None = None,
) -> bool:
    if segment_start is None:
        segment_start = max(0, start - 12)
    if segment_end is None:
        segment_end = min(len(text), end + 12)
    seg = text[segment_start:segment_end]
    return any(k in seg for k in _STAT_TRAK_KEYWORDS)


# ============================================================
# 組裝
# ============================================================
def _build_mhn(weapon: str | None, skin: str, wear: str | None,
               stattrak: bool, star: bool) -> str:
    prefix = "★ " if star else ""
    if stattrak:
        prefix += "StatTrak™ "
    core = f"{weapon} | {skin}" if weapon else skin
    if wear:
        core += f" ({wear})"
    return f"{prefix}{core}"


def _star_needed(weapon: str | None) -> bool:
    return weapon in STAR_WEAPONS


# ============================================================
# 主解析
# ============================================================
def parse_item_candidates(
    text: str,
    *,
    full_dict: dict[str, str],
    pattern_dict: dict[str, str],
    weapon_map: dict[str, str],
) -> list[ItemCandidate]:
    if not text or not text.strip():
        return []

    # 1. 收集所有原始 match span（不第一命中 return）
    # Phase P2.1：命中需詞邊界（前後不得是字母/數字/中文）——避免
    # 「半件AK-47 | 红线複製品」這類 substring 被當 exact 商品命中
    def _has_boundary(txt: str, start: int, end: int) -> bool:
        before = txt[start - 1] if start > 0 else ""
        after = txt[end] if end < len(txt) else ""
        return not (before and (before.isalnum() or "\u4e00" <= before <= "\u9fff")) \
            and not (after and (after.isalnum() or "\u4e00" <= after <= "\u9fff"))

    spans: list[tuple[int, int, str, str, str]] = []  # (start, end, kind, cn, en)
    for cn_full, en_full in full_dict.items():
        if len(cn_full) < 2:
            continue
        idx = text.find(cn_full)
        while idx != -1:
            if _has_boundary(text, idx, idx + len(cn_full)):
                spans.append((idx, idx + len(cn_full), "full", cn_full, en_full))
            idx = text.find(cn_full, idx + 1)
    # pattern 命中不要求詞邊界（「14卡托红线」等術語正常）——
    # pattern candidate 一律 unverified，由 canonical 驗證把關
    for cn, en in pattern_dict.items():
        if len(cn) < 2:
            continue
        idx = text.find(cn)
        while idx != -1:
            spans.append((idx, idx + len(cn), "pattern", cn, en))
            idx = text.find(cn, idx + 1)

    if not spans:
        return []

    item_spans = [(s, e) for s, e, *_ in spans]

    # 2. 排序後逐候選計算 segment，並在 segment 內偵測
    raw: list[ItemCandidate] = []
    for s, e, kind, cn, en in sorted(spans, key=lambda x: (x[0], x[1])):
        seg_start, seg_end = find_candidate_segment(text, s, e, item_spans)

        role = detect_role(text, s, e, segment_start=seg_start, segment_end=seg_end)
        wear = detect_wear(text, s, e, segment_start=seg_start, segment_end=seg_end)
        stattrak = detect_stattrak(text, s, e, segment_start=seg_start, segment_end=seg_end)

        if kind == "full":
            # full_dict 候選：weapon/skin 從英文名拆
            core = en.replace("StatTrak™", "").replace("★ ", "").strip()
            if "|" in core:
                weapon, skin = [p.strip() for p in core.split("|", 1)]
            else:
                weapon, skin = None, core
            star = "★" in en or _star_needed(weapon)
            if stattrak and "StatTrak" not in en:
                mhn = f"★ StatTrak™ {core}" if star else f"StatTrak™ {core}"
            else:
                mhn = en
            if wear:
                mhn = f"{mhn} ({wear})"
            raw.append(ItemCandidate(
                market_hash_name=mhn,
                weapon=weapon,
                skin=skin,
                wear=wear,
                stattrak=stattrak,
                role=role,
                original_text=text,
                matched_key=cn,
                match_start=s,
                match_end=e,
                parser="item_parser",
                evidence=ItemEvidence.DICT_FULL,
                confidence=0.95,
                score=100.0,
                # Phase P2：受信任字典 exact 命中 → verified
                verified=True,
                verified_by="trusted_dictionary_exact",
            ))
        else:
            # pattern 候選：segment 內找武器
            weapon, _ = find_weapon(text, s, e, weapon_map,
                                    segment_start=seg_start, segment_end=seg_end)
            star = _star_needed(weapon)
            mhn = _build_mhn(weapon, en, wear, stattrak, star)
            # Phase P2.1：pattern 命中預設 unverified（candidate 語意——
            # validation_error 留給 ItemValidator 正式驗證結果；
            # 最終由 process_posts 的 hard gate 阻擋未驗證查價）
            p_verified = False
            p_verified_by = None
            p_validation_error = None
            if weapon and mhn:
                try:
                    if _get_parser_validator().validate_market_name(mhn):
                        p_verified = True
                        p_verified_by = "canonical_catalog"
                        p_validation_error = None
                except RuntimeError:
                    # catalog 不可用 → fail-closed unverified
                    pass
            raw.append(ItemCandidate(
                market_hash_name=mhn,
                weapon=weapon,
                skin=en,
                wear=wear,
                stattrak=stattrak,
                role=role,
                original_text=text,
                matched_key=cn,
                match_start=s,
                match_end=e,
                parser="item_parser",
                evidence=ItemEvidence.DICT_PATTERN,
                confidence=0.85 if weapon else 0.60,
                score=(84.0 if weapon else 62.0),
                verified=p_verified,
                verified_by=p_verified_by,
                validation_error=p_validation_error,
            ))

    return _dedup(raw)


def _intervals_overlap(a_start, a_end, b_start, b_end) -> bool:
    if a_start is None or a_end is None or b_start is None or b_end is None:
        return False
    return a_start < b_end and b_start < a_end


def _same_product(a: ItemCandidate, b: ItemCandidate) -> bool:
    """視為同一商品的條件：skin 相同，且（武器都無 或 武器相同）。"""
    if a.skin != b.skin:
        return False
    if a.weapon and b.weapon:
        return a.weapon == b.weapon
    return True


def _strength(c: ItemCandidate) -> tuple[int, int]:
    """排序強度：evidence 優先、長 key 優先。"""
    ev = {ItemEvidence.DICT_FULL: 100, ItemEvidence.DICT_PATTERN: 80}.get(
        c.evidence, 0)
    key_len = len(c.matched_key or "")
    return (ev, key_len)


def _dedup(candidates: list[ItemCandidate]) -> list[ItemCandidate]:
    """去重：
    - 完整名優先於花紋
    - 較長 matched_key 優先於較短
    - 重疊區間且同商品 → 保留較強候選
    - 不同位置的相同商品可保留
    """
    accepted: list[ItemCandidate] = []
    for c in sorted(candidates, key=_strength, reverse=True):
        dup = any(
            _intervals_overlap(c.match_start, c.match_end, a.match_start, a.match_end)
            and _same_product(c, a)
            for a in accepted
        )
        if not dup:
            accepted.append(c)
    return accepted