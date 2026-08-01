"""
test_item_parser.py — 商品候選解析器測試（Phase 3）

使用測試專用字典（不修改正式 skin_dict.json）。
驗證：多候選收集、不第一命中 return、角色判斷、磨損、StatTrak、star prefix。
"""
import sys
import os

import pytest

PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from alkaid_cs2.domain.item_candidate import ItemEvidence, ItemRole  # noqa: E402
from alkaid_cs2.parsers.item_parser import parse_item_candidates  # noqa: E402

# ── 測試字典（不修改正式 skin_dict.json）──
FULL_DICT = {
    "AK-47 | 红线": "AK-47 | Redline",
    "AK-47 | 火神": "AK-47 | Vulcan",
    "AWP | 巨龙传说": "AWP | Dragon Lore",
}

PATTERN_DICT = {
    "红线": "Redline",
    "紅線": "Redline",       # 繁體 key（測試 parser 支援，不動正式字典）
    "红线行动": "Redline Action",  # 長 key（測試長字串優先）
    "火神": "Vulcan",
    "鈷分裂": "Cobalt Disruption",
    "電擊": "Electric Hive",
    "虎牙": "Tiger Tooth",
    "漸層": "Fade",
}

WEAPON_MAP = {
    "AK-47": "AK-47", "ak": "AK-47", "阿卡": "AK-47",
    "沙鷹": "Desert Eagle", "沙鹰": "Desert Eagle",
    "爪子刀": "Karambit", "蝴蝶刀": "Butterfly Knife",
    "AWP": "AWP",
}


def parse(text):
    return parse_item_candidates(
        text, full_dict=FULL_DICT, pattern_dict=PATTERN_DICT, weapon_map=WEAPON_MAP
    )


# ---------------------------------------------------------------
# 1. 單商品
# ---------------------------------------------------------------
def test_single_item():
    cands = parse("售 AK-47 | 红线 久经沙场 5000")
    assert len(cands) == 1, f"cands={cands}"
    c = cands[0]
    assert c.skin == "Redline"
    assert c.weapon == "AK-47"
    assert c.wear == "Field-Tested"
    assert c.role is ItemRole.SELLING
    assert c.evidence is ItemEvidence.DICT_FULL


# ---------------------------------------------------------------
# 2. 紅線與火神同文 → 同時產生兩個候選
# ---------------------------------------------------------------
def test_redline_and_vulcan():
    cands = parse("出2把傳家寶ak 14卡托红线 7480 火神4xtitan 14000")
    skins = {c.skin for c in cands}
    assert "Redline" in skins, f"skins={skins}"
    assert "Vulcan" in skins, f"skins={skins}"
    assert len(cands) >= 2


# ---------------------------------------------------------------
# 3. 繁體紅線（測試字典含繁體 key，parser 支援）
# ---------------------------------------------------------------
def test_traditional_redline():
    cands = parse("售 AK-47 紅線 久經 5000")
    assert any(c.skin == "Redline" for c in cands), f"cands={cands}"


# ---------------------------------------------------------------
# 4. selling + buying 同文
# ---------------------------------------------------------------
def test_selling_and_buying():
    cands = parse("售 沙鷹鈷分裂 5000 收 AWP 電擊 3000")
    roles = {(c.skin, c.role) for c in cands}
    assert ("Cobalt Disruption", ItemRole.SELLING) in roles, f"roles={roles}"
    assert ("Electric Hive", ItemRole.BUYING) in roles, f"roles={roles}"


# ---------------------------------------------------------------
# 5. trade（貼換）→ 非 SELLING
# ---------------------------------------------------------------
def test_trade_post():
    cands = parse("紅線貼換火神")
    assert len(cands) >= 2
    for c in cands:
        assert c.role is not ItemRole.SELLING, f"role={c.role} skin={c.skin}"
    assert all(c.role is ItemRole.TRADE for c in cands), f"roles={[c.role for c in cands]}"


# ---------------------------------------------------------------
# 6. reference（參考/同磨底）→ 不得都 SELLING
# ---------------------------------------------------------------
def test_reference_post():
    cands = parse("紅線參考火神同磨底")
    assert len(cands) >= 2
    selling = [c for c in cands if c.role is ItemRole.SELLING]
    assert len(selling) == 0, f"誤判 SELLING: {selling}"
    assert all(c.role is ItemRole.REFERENCE for c in cands), f"roles={[c.role for c in cands]}"


# ---------------------------------------------------------------
# 7. 完整名稱優先於花紋（同商品重疊 → 只留 full）
# ---------------------------------------------------------------
def test_full_beats_pattern():
    cands = parse("AK-47 | 红线")
    assert len(cands) == 1, f"cands={cands}"
    assert cands[0].evidence is ItemEvidence.DICT_FULL
    assert cands[0].skin == "Redline"


# ---------------------------------------------------------------
# 8. 長 key 優先（重疊時保留較長 matched_key）
# ---------------------------------------------------------------
def test_longer_key_preferred():
    cands = parse("红线行动 5000")
    skins = {c.skin for c in cands}
    assert "Redline Action" in skins, f"skins={skins}"
    # 「红线」被長 key「红线行动」覆蓋（重疊同商品）→ 不得同時出現 Redline
    assert "Redline" not in skins or cands[0].skin == "Redline Action"


# ---------------------------------------------------------------
# 9. 磨損：繁/簡 + 無磨損 → None
# ---------------------------------------------------------------
def test_wear_variants():
    c1 = parse("售 AK-47 红线 嶄新出廠 5000")
    assert c1[0].wear == "Factory New", f"wear={c1[0].wear}"
    c2 = parse("售 AK-47 红线 久经沙场 5000")
    assert c2[0].wear == "Field-Tested"
    # 無磨損 → None（不得預設 Field-Tested）
    c3 = parse("售 AK-47 红线 5000")
    assert c3[0].wear is None, f"wear={c3[0].wear}"


# ---------------------------------------------------------------
# 10. StatTrak
# ---------------------------------------------------------------
def test_stattrak():
    cands = parse("暗金 AK-47 红线")
    c = [x for x in cands if x.skin == "Redline"][0]
    assert c.stattrak is True
    assert "StatTrak" in (c.market_hash_name or "")


# ---------------------------------------------------------------
# 11. Knife star prefix
# ---------------------------------------------------------------
def test_knife_star_prefix():
    cands = parse("爪子刀 虎牙 崭新出厂")
    c = [x for x in cands if x.skin == "Tiger Tooth"][0]
    assert c.weapon == "Karambit"
    assert c.market_hash_name == "★ Karambit | Tiger Tooth (Factory New)"
    assert c.wear == "Factory New"


# ---------------------------------------------------------------
# 12. 空字串 → []
# ---------------------------------------------------------------
def test_empty_text():
    assert parse("") == []
    assert parse("   ") == []


# ---------------------------------------------------------------
# 13. 不相關貼文 → []
# ---------------------------------------------------------------
def test_unrelated_text():
    assert parse("收個雞蛋 有人有嗎") == []


# ---------------------------------------------------------------
# 14. 同一商品重複文字 → 不得產生完全相同重複候選
# ---------------------------------------------------------------
def test_no_identical_duplicates():
    cands = parse("售 AK-47 | 红线 红线")
    # 轉成可比較 tuple，確認沒有完全相同的兩筆
    seen = set()
    for c in cands:
        key = (c.market_hash_name, c.match_start, c.match_end, c.evidence)
        assert key not in seen, f"重複候選: {key}"
        seen.add(key)
    # full 命中 + 位置 7 的紅線（不重疊）→ 至多 2 筆
    assert len(cands) <= 2


# ================================================================
# Phase 3.1 — segment 強化測試
# ================================================================
# 15. 分句角色隔離（。分隔）
# ---------------------------------------------------------------
def test_sentence_role_isolation():
    cands = parse("售 AK-47 红线 5000。收 AWP 電擊 3000")
    roles = {(c.skin, c.role) for c in cands}
    assert ("Redline", ItemRole.SELLING) in roles, f"roles={roles}"
    assert ("Electric Hive", ItemRole.BUYING) in roles, f"roles={roles}"


# ---------------------------------------------------------------
# 16. 換行角色隔離
# ---------------------------------------------------------------
def test_newline_role_isolation():
    cands = parse("售 AK-47 红线 5000\n收 AWP 電擊 3000")
    roles = {(c.skin, c.role) for c in cands}
    assert ("Redline", ItemRole.SELLING) in roles
    assert ("Electric Hive", ItemRole.BUYING) in roles


# ---------------------------------------------------------------
# 17. trade 雙向（貼換）
# ---------------------------------------------------------------
def test_trade_bidirectional():
    cands = parse("紅線貼換火神")
    roles = {(c.skin, c.role) for c in cands}
    assert ("Redline", ItemRole.TRADE) in roles, f"roles={roles}"
    assert ("Vulcan", ItemRole.TRADE) in roles, f"roles={roles}"


# ---------------------------------------------------------------
# 18. reference 不污染 selling（；分隔）
# ---------------------------------------------------------------
def test_reference_does_not_pollute_selling():
    cands = parse("售 紅線 5000；火神僅供參考")
    roles = {(c.skin, c.role) for c in cands}
    assert ("Redline", ItemRole.SELLING) in roles, f"roles={roles}"
    assert ("Vulcan", ItemRole.REFERENCE) in roles, f"roles={roles}"


# ---------------------------------------------------------------
# 19. 武器跨段隔離
# ---------------------------------------------------------------
def test_weapon_segment_isolation():
    cands = parse("AK-47 紅線 5000。AWP 電擊 3000")
    weapons = {(c.skin, c.weapon) for c in cands}
    assert ("Redline", "AK-47") in weapons, f"weapons={weapons}"
    assert ("Electric Hive", "AWP") in weapons, f"weapons={weapons}"


# ---------------------------------------------------------------
# 20. 武器距離超過舊 15 字但在同一區段 → 仍配對
# ---------------------------------------------------------------
def test_weapon_far_but_same_segment():
    text = "AK-47 這是一把傳家寶級別的槍 塗裝非常漂亮 帶有名牌 紅線 5000"
    cands = parse(text)
    redline = [c for c in cands if c.skin == "Redline"]
    assert redline, "紅線未命中"
    assert redline[0].weapon == "AK-47", f"weapon={redline[0].weapon}"


# ---------------------------------------------------------------
# 21. 不得借用下一件商品的武器
# ---------------------------------------------------------------
def test_no_weapon_from_next_item():
    cands = parse("紅線 5000。AWP 電擊 3000")
    weapons = {(c.skin, c.weapon) for c in cands}
    assert ("Redline", None) in weapons, f"weapons={weapons}"
    assert ("Electric Hive", "AWP") in weapons, f"weapons={weapons}"


# ---------------------------------------------------------------
# 22. matched_text 必須等於原文切片
# ---------------------------------------------------------------
def test_matched_text_equals_slice():
    cands = parse("售 AK-47 | 红线 久经沙场 5000")
    for c in cands:
        assert c.matched_text is not None
        assert c.original_text[c.match_start:c.match_end] == c.matched_text


# ---------------------------------------------------------------
# 23. 原 Phase 3 的 14 個 parser tests 仍通過（重點回歸抽查）
# ---------------------------------------------------------------
def test_phase3_regression_single_item():
    cands = parse("售 AK-47 | 红线 久经沙场 5000")
    assert len(cands) == 1
    assert cands[0].skin == "Redline"
    assert cands[0].wear == "Field-Tested"


def test_phase3_regression_redline_vulcan():
    cands = parse("出2把傳家寶ak 14卡托红线 7480 火神4xtitan 14000")
    skins = {c.skin for c in cands}
    assert "Redline" in skins and "Vulcan" in skins


def test_phase3_regression_full_beats_pattern():
    cands = parse("AK-47 | 红线")
    assert len(cands) == 1
    assert cands[0].evidence is ItemEvidence.DICT_FULL


def test_phase3_regression_knife_star():
    cands = parse("爪子刀 虎牙 崭新出厂")
    c = [x for x in cands if x.skin == "Tiger Tooth"][0]
    assert c.market_hash_name == "★ Karambit | Tiger Tooth (Factory New)"
