"""
legacy_adapter.py — Phase 0 legacy snapshot adapter
====================================================
Invokes the CURRENT production parsing flow (analyze_arbitrage.extract_skin_info)
without modifying it. Produces a normalized snapshot for regression comparison.

This adapter exists so Phase 1+ can compare new behavior against the exact
behavior captured today. It must NOT change any production code.
"""
import os
import sys

# 專案根目錄（tests/regression/ -> 專案根）
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import analyze_arbitrage as aa  # noqa: E402


class LegacyExtractionError(Exception):
    pass


def extract_legacy(post_text: str, *, verify_fn=None) -> dict:
    """
    呼叫現有 extract_skin_info() 並標準化輸出。

    Args:
        post_text: 貼文文字
        verify_fn: 測試用驗證函式覆寫（None=用正式 _verify_skin_on_csgoskins）

    Returns:
        {
          "status": "ok" | "unresolved",
          "market_hash_name": str | None,
          "seller_price": int | -1,
          "currency": str | None,   # legacy 無此概念 → 字典命中時 None
          "confidence": str | None,
        }
    """
    original_verify = aa._verify_skin_on_csgoskins
    if verify_fn is not None:
        aa._verify_skin_on_csgoskins = verify_fn
    try:
        info = aa.extract_skin_info(post_text)
    finally:
        aa._verify_skin_on_csgoskins = original_verify

    if info is None or info.get("verified") is not True or \
            not info.get("market_hash_name"):
        # Phase P2：未驗證 / unresolved 結構 → unresolved
        return {
            "status": "unresolved",
            "market_hash_name": None,
            "seller_price": info.get("seller_price", -1) if info else -1,
            "currency": None,
            "confidence": info.get("confidence") if info else None,
        }

    return {
        "status": "ok",
        "market_hash_name": info.get("market_hash_name"),
        "seller_price": info.get("seller_price", -1),
        # legacy 無 currency 欄位；字典命中路徑視為 TWD（已知缺陷，見 fixture 6）
        "currency": None,
        "confidence": info.get("confidence"),
    }


def parse_market_hash(mhn: str | None) -> dict:
    """
    把 market_hash_name 拆成 weapon/skin/wear/stattrak 欄位（供比對用）。
    Ex: "★ StatTrak™ Karambit | Tiger Tooth (Factory New)"
    """
    if not mhn:
        return {"weapon": None, "skin": None, "wear": None, "stattrak": None}

    stattrak = "StatTrak" in mhn
    star = "★" in mhn

    core = mhn.replace("StatTrak™", "").replace("★", "").strip()

    # 磨損
    wear = None
    import re
    m = re.search(r"\((Factory New|Minimal Wear|Field-Tested|Well-Worn|Battle-Scarred)\)\s*$", core)
    if m:
        wear = m.group(1)
        core = core[: m.start()].strip()

    # 武器 | 花紋
    if "|" in core:
        weapon, skin = [p.strip() for p in core.split("|", 1)]
    else:
        weapon, skin = None, core.strip()

    return {"weapon": weapon, "skin": skin, "wear": wear, "stattrak": stattrak, "star": star}
