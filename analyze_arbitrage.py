"""
analyze_arbitrage.py - CS2 搬磚套利分析機器人 (v2)
===================================================
本地 Playwright 爬蟲 → DeepSeek 解析 → 對比 BUFF 價格 → 套利評估

運作機制:
  1. Playwright 無頭模式,複製 Chrome 登入狀態進入 FB 不公開社團
  2. 抓取最新貼文,比對 SQLite 去重
  3. DeepSeek 分析套利潛力
  4. 預留雲端同步接口 (Supabase)

使用方法:
  export DEEPSEEK_API_KEY=sk-...
  python analyze_arbitrage.py
"""

import os
import sys
import json
import time
import sqlite3
import re
from datetime import datetime, time as dtime

from alkaid_cs2.services.item_validator import (  # noqa: E402
    require_verified_market_item,
)

# ============================================================
# 路徑設定
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
CONFIG_PATH = os.path.join(BASE_DIR, "config.txt")
DB_PATH = os.path.join(BASE_DIR, "cs2_prices.db")
STATE_PATH = os.path.join(BASE_DIR, "analyzer_state.json")

# ============================================================
# config.txt 讀取
# ============================================================

def _read_config(key: str, default=""):
    if not os.path.exists(CONFIG_PATH):
        return default
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line.startswith(f"{key}="):
                return line[len(key) + 1:].strip()
    return default
API_KEY = ""
API_BASE = ""
MODEL = ""

# 優先使用 DeepSeek 直連(無 rate limit)
if os.environ.get("DEEPSEEK_API_KEY"):
    API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
    API_BASE = "https://api.deepseek.com/v1"
    MODEL = "deepseek-chat"
# 後備:OpenRouter
elif os.environ.get("OPENROUTER_API_KEY"):
    API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
    API_BASE = "https://openrouter.ai/api/v1"
    MODEL = "deepseek/deepseek-chat"

DEALS_HISTORY_PATH = os.path.join(BASE_DIR, "deals_history.json")
BUFF_FEE_RATE = 0.985
MIN_VOLUME = 50
MAX_DEALS_HISTORY = 50  # 最多保留最近 50 筆

FB_COOKIE_CACHED = None

# === Playwright 設定 ===
PLAYWRIGHT_USER_DATA_DIR = _read_config("PLAYWRIGHT_USER_DATA_DIR",
    os.path.join(os.environ.get("LOCALAPPDATA", "C:\\Users\\user\\AppData\\Local"),
                 "Google", "Chrome", "User Data"))
FB_GROUP_URLS = [u.strip() for u in _read_config("FB_GROUP_URLS", "").split(",") if u.strip()]

# ============================================================
# 去重狀態管理
# ============================================================

def load_state() -> dict:
    default = {"processed_ids": [], "total_scanned_today": 0, "last_fetch": "", "date": ""}
    if not os.path.exists(STATE_PATH):
        return default
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if data.get("date") != datetime.now().strftime("%Y-%m-%d"):
                data["total_scanned_today"] = 0
                data["date"] = datetime.now().strftime("%Y-%m-%d")
            return data
    except (json.JSONDecodeError, IOError):
        return default

def save_state(state: dict):
    state["date"] = datetime.now().strftime("%Y-%m-%d")
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

def mark_processed(post_ids: list[str], state: dict):
    processed = set(state.get("processed_ids", []))
    for pid in post_ids:
        if pid:
            processed.add(pid)
    state["processed_ids"] = list(processed)[-5000:]
    state["total_scanned_today"] = state.get("total_scanned_today", 0) + len(post_ids)

def filter_new_posts(posts: list[dict], state: dict) -> list[dict]:
    processed = set(state.get("processed_ids", []))
    return [p for p in posts if p.get("id") and p["id"] not in processed]

# ============================================================
# Playwright 爬蟲(模組 C)
# ============================================================

def fetch_fb_posts() -> list[dict]:
    """抓取 FB 社團賣家貼文(逐篇審查模式).

    Returns:
        list[dict]: 每筆含 id, author, content, link.
    """
    if not FB_GROUP_URLS:
        print("  ⚠️  config.txt 未設定 FB_GROUP_URLS")
        return []

    try:
        import cdp_fb_crawler as cfc
        return cfc.fetch_posts(max_scrolls=50, max_posts=15)
    except Exception as e:
        print(f"  [FB] ❌ 新爬蟲失敗: {e}")
        return []


def _fallback_simulated() -> list[dict]:

    # 重整頁面確保最新貼文
    send("Page.reload", {"ignoreCache": True})
    print("  [CDP] 🔄 重整頁面,等待載入...")
    time.sleep(5)

    # 設定 viewport + 80% 縮放(一圖更多貼文,減少重複)
    send("Emulation.setDeviceMetricsOverride", {
        "width": 1280, "height": 2000,
        "deviceScaleFactor": 1,
        "mobile": False,
    })
    send("Page.setZoomFactor", {"zoomFactor": 0.8})
    time.sleep(1)

    # 先切到「商品買賣」分頁(只顯示交易貼文)
    send("Runtime.evaluate", {
        "expression": """
(()=>{
    const tabs = [...document.querySelectorAll('[role="tab"]')];
    const target = tabs.find(el => el.innerText.includes('商品買賣'));
    if(target) { target.click(); return 'OK'; }
    return 'not found';
})();
""", "returnByValue": True
    })
    time.sleep(2)

    # 再切到「新貼文」排序
    send("Input.dispatchKeyEvent", {"type": "keyDown", "windowsVirtualKeyCode": 27})  # Escape
    time.sleep(0.3)
    # 關閉可能擋畫面的下拉選單
    send("Runtime.evaluate", {
        "expression": """
(()=>{
    // 先點擊排序按鈕開啟選單
    const btn = [...document.querySelectorAll('[role="tab"],[role="button"]')]
        .find(el => el.innerText.includes('新貼文'));
    if(btn) btn.click();
    // 等選單出現
    setTimeout(() => {
        // 點選「新貼文」選項
        const opt = [...document.querySelectorAll('[role="menuitem"],[role="option"]')]
            .find(el => el.innerText.includes('新貼文'));
        if(opt) opt.click();
    }, 300);
})();
""", "returnByValue": True
    })
    time.sleep(2)
    # 點其他地方關閉選單
    send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": 10, "y": 10, "button": "left", "clickCount": 1})
    send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": 10, "y": 10, "button": "left", "clickCount": 1})
    time.sleep(1)

    # 滾動觸發載入
    for _ in range(5):
        send("Input.synthesizeScrollGesture", {
            "x": 400, "y": 400, "xDistance": 0, "yDistance": -500,
            "xOverscroll": 0, "yOverscroll": 0
        })
        time.sleep(random.uniform(1.0, 1.8))

    # 回到頂端 → 邊滾邊截
    send("Runtime.evaluate", {"expression": "window.scrollTo(0,0)", "returnByValue": True})
    time.sleep(1)

    # 先截第一張
    all_posts = []
    seen_keys = set()

    def capture_and_parse():
        result = send("Page.captureScreenshot", {"format": "png"})
        b64 = result.get('result', {}).get('data', '')
        if not b64:
            return
        from PIL import Image
        import io, base64
        buf = io.BytesIO()
        Image.open(io.BytesIO(base64.b64decode(b64))).save(buf, format="JPEG", quality=80)
        try:
            import vision_analyzer as va
            vp = ("這是FB商品買賣截圖,找出所有CS2交易貼文(要賣東西的才算).\n"
                  "每則回傳JSON陣列:\n"
                  "1. author: 作者\n2. market_hash_name: 皮膚英文含磨損\n"
                  "3. seller_price: 台幣價格(無價格回傳-1)\n只回傳[]如果沒有交易貼文.")
            items = va.analyze_image(buf.getvalue(), custom_prompt=vp, retry=1)
        except Exception:
            items = []
        if items and isinstance(items, list):
            for item in items:
                auth = item.get('author', '')
                mh = item.get('market_hash_name', '')
                price = item.get('seller_price', -1)
                try:
                    pv = int(float(str(price).replace(',', '')))
                except:
                    pv = -1
                key = f"{auth[:4]}|{mh}|{pv}"
                if mh and mh != 'UNKNOWN' and key not in seen_keys:
                    seen_keys.add(key)
                    all_posts.append(item)

    # 第一張:初始畫面
    capture_and_parse()

    # 邊滾邊截:每 2 次滾動截一張
    for i in range(12):
        for _ in range(2):
            send("Input.synthesizeScrollGesture", {
                "x": 600, "y": 600, "xDistance": 0, "yDistance": -600,
                "xOverscroll": 0, "yOverscroll": 0
            })
            time.sleep(0.5)
        capture_and_parse()
        print(f"  [CDP] 📸 第 {i+2} 張截圖...")

    ws.close()

    if not all_posts:
        print("  [CDP] ⚠️  未找到交易貼文")
        return []

    # 整理結果 - 合併同作者 + 同價格的貼文
    from collections import defaultdict
    price_groups = defaultdict(list)
    for item in all_posts:
        mh = item.get('market_hash_name', '')
        auth = item.get('author', '未知')
        price = item.get('seller_price', -1)
        try:
            price = int(float(str(price).replace(',', '')))
        except:
            price = -1
        if not mh or mh == 'UNKNOWN' or price <= 0:
            continue
        price_groups[f"{auth[:4]}|{price}"].append(item)

    posts = []
    for key, group in price_groups.items():
        auth = group[0].get('author', '未知')
        price = int(float(str(group[0].get('seller_price', '0')).replace(',', '')))
        skins = [g.get('market_hash_name', '') for g in group]
        skin_text = skins[0] if len(skins) == 1 else f"{skins[0]} 等 {len(skins)} 件"
        posts.append({
            "id": f"cdp_{len(posts)}",
            "author": auth,
            "content": f"【售】{skin_text}\n賣 {price} 台幣",
            "link": FB_GROUP_URLS[0] if FB_GROUP_URLS else "",
        })

    print(f"  [CDP+Vision] ✅ 深滾多截圖 → {len(posts)} 篇有價格貼文")
    return posts

def _fallback_simulated() -> list[dict]:
    """後備:模擬貼文(無 Playwright 或抓取失敗時使用)."""
    dummy = [
        {"id": "sim_001", "author": "陳小明",
         "content": "【售】AK-47 火蛇 略磨 0.13\n賣 21000台幣 可小議\nhttps://facebook.com/groups/cs2tw/001",
         "link": "https://facebook.com/groups/cs2tw/001"},
        {"id": "sim_002", "author": "林阿豪",
         "content": "【降價】AWP 二西莫夫 久經\n賣 3200 台幣\nhttps://facebook.com/groups/cs2tw/002",
         "link": "https://facebook.com/groups/cs2tw/002"},
        {"id": "sim_003", "author": "王小明",
         "content": "【售】M4A1 印花集 嶄新\n佛心價 12000 台幣\nhttps://facebook.com/groups/cs2tw/003",
         "link": "https://facebook.com/groups/cs2tw/003"},
        {"id": "sim_004", "author": "張大帥",
         "content": "【售】AK-47 紅線 久經\n賣 1200台幣\nhttps://facebook.com/groups/cs2tw/004",
         "link": "https://facebook.com/groups/cs2tw/004"},
        {"id": "sim_005", "author": "刀王",
         "content": "【售】蝴蝶刀 漸變 嶄新\n賣 64000台幣\nhttps://facebook.com/groups/cs2tw/006",
         "link": "https://facebook.com/groups/cs2tw/006"},
        {"id": "sim_006", "author": "平轉仔",
         "content": "【售】AK-47 火蛇 久經\n平轉 22000台幣 不議價\nhttps://facebook.com/groups/cs2tw/007",
         "link": "https://facebook.com/groups/cs2tw/007"},
    ]
    print(f"  [模擬] 使用 {len(dummy)} 篇模擬貼文")
    return dummy


def get_scan_stats() -> dict:
    state = load_state()
    source = "Playwright" if FB_GROUP_URLS else "模擬貼文"
    return {
        "last_fetch": state.get("last_fetch", "從未"),
        "scanned_today": state.get("total_scanned_today", 0),
        "source": source,
    }

# ============================================================
# DeepSeek 客戶端
# ============================================================

def create_client():
    from openai import OpenAI
    if not API_KEY:
        print("❌ 錯誤:未設定 DEEPSEEK_API_KEY")
        return None
    return OpenAI(api_key=API_KEY, base_url=API_BASE)


def _wear_to_en(text: str) -> str:
    """磨損度中英對照 (Steam 官方翻譯, 注意簡繁相反陷阱)

    官方對照:
      Factory New    = 嶄新出廠(繁) / 崭新出厂(簡) / 厂新 / 全新
      Minimal Wear   = 輕微磨損(繁) / 略有磨损(簡)
      Field-Tested   = 久經沙場(繁) / 久经沙场(簡)
      Well-Worn      = 戰痕累累(繁) / 破损不堪(簡)  ← 相反!
      Battle-Scarred = 破損不堪(繁) / 战痕累累(簡)  ← 相反!
    """
    t = text
    # 優先精確匹配
    if any(w in t for w in ["崭新出厂", "嶄新出廠", "厂新", "全新", "崭新", "嶄新", "FN"]):
        return "Factory New"
    if any(w in t for w in ["略有磨损", "略有磨損", "輕微磨損", "轻微磨损", "MW"]):
        return "Minimal Wear"
    if any(w in t for w in ["久经沙场", "久經沙場", "久經沙場", "久经", "久經", "FT"]):
        return "Field-Tested"
    # 關鍵: 簡繁相反
    if "战痕累累" in t:   # 簡體 = Battle-Scarred
        return "Battle-Scarred"
    if "破損不堪" in t:   # 繁體 = Battle-Scarred
        return "Battle-Scarred"
    if "破损不堪" in t:   # 簡體 = Well-Worn
        return "Well-Worn"
    if "戰痕累累" in t:   # 繁體 = Well-Worn
        return "Well-Worn"
    if any(w in t for w in ["戰痕", "战痕", "BS"]):
        return "Battle-Scarred"
    if any(w in t for w in ["破損", "破损", "WW"]):
        return "Well-Worn"
    return "Field-Tested"  # 預設


# ============================================================
# V2 bridge 用武器對照（與 extract_skin_info 內部 weapon_map 相同，供 Phase 6.2 bridge 使用）
# ============================================================
_V2_WEAPON_MAP = {
    "AK-47": "AK-47", "ak": "AK-47", "阿卡": "AK-47",
    "M4A1-S": "M4A1-S", "M4A4": "M4A4", "沙鹰": "Desert Eagle", "沙鷹": "Desert Eagle",
    "蝴蝶刀": "Butterfly Knife", "爪子刀": "Karambit", "爪刀": "Karambit",
    "刺刀": "Bayonet", "折刀": "Flip Knife", "锯齿爪刀": "Huntsman Knife",
    "穿肠刀": "Gut Knife", "猎刀": "Huntsman Knife",
    "猎杀者": "Huntsman Knife", "系绳者": "Talon Knife", "穿刺者": "Stiletto Knife",
    "求生匕首": "Survival Knife", "流浪者": "Nomad Knife", "骷髅匕首": "Skeleton Knife",
    "运动手套": "Sport Gloves", "專業手套": "Specialist Gloves",
    "专业手套": "Specialist Gloves", "驾驶手套": "Driver Gloves", "駕駛手套": "Driver Gloves",
    "摩托手套": "Moto Gloves", "手部束带": "Hand Wraps", "手部束帶": "Hand Wraps",
    "血猎手套": "Bloodhound Gloves", "血獵手套": "Bloodhound Gloves",
    "裹手": "Hand Wraps", "沙漠之鹰": "Desert Eagle",
}


def _load_v2_dicts() -> tuple[dict, dict]:
    """載入 skin_dict.json 的 full / pattern 字典供 V2 bridge 使用。"""
    dict_path = os.path.join(BASE_DIR, "skin_dict.json")
    if os.path.exists(dict_path):
        with open(dict_path, "r", encoding="utf-8") as f:
            dict_data = json.load(f)
        return (
            dict_data.get("full_cn_to_en", {}),
            dict_data.get("pattern_cn_to_en", {}),
        )
    return {}, {}


_LEGACY_VALIDATOR = None


# Phase P1/P1.1：唯一換算邊界（config helper；rate 集中、可注入）
from alkaid_cs2.services.currency import (
    CurrencyService,
    UnsupportedCurrencyError,
    AlreadyConvertedError,
    MoneyValidationError,
)
from alkaid_cs2.domain.price import (
    Money,
    ConvertedMoney,
)
from alkaid_cs2.domain.enums import Currency
from decimal import Decimal as _Decimal, ROUND_HALF_UP as _ROUND_HALF_UP
from dataclasses import dataclass


def build_legacy_currency_service(
    *,
    rmb_to_twd=_Decimal("4.5"),
    usd_to_rmb=_Decimal("7.2"),
    rate_source="legacy-static-rate",
) -> CurrencyService:
    """P1.1：集中 rate config helper（測試可注入 fixture rate）。"""
    return CurrencyService(
        rmb_to_twd=rmb_to_twd,
        usd_to_rmb=usd_to_rmb,
        rate_source=rate_source,
    )


_CURRENCY_SERVICE = build_legacy_currency_service()


@dataclass(frozen=True)
class SellerAskConversionResult:
    """P1.1：換算結果（成功/失敗不可誤判）。"""

    original: Money | None
    converted: ConvertedMoney | None
    valid: bool
    error_code: str | None


def parse_original_amount(value: object) -> _Decimal:
    """P1.1：嚴格原始金額輸入邊界（保留完整十進位精度）。

    允許：Decimal / int（非 bool）/ 安全十進位字串（可含逗號）。
    拒絕：float / bool / None / 空字串 / NaN / Infinity / 科學記號 /
          含貨幣文字 / 負數 / 任意 object。
    """
    if isinstance(value, bool):
        raise TypeError("金額不接受 bool")
    if isinstance(value, float):
        raise TypeError("金額不接受 float，請用 str/int（Decimal 精度）")
    if isinstance(value, _Decimal):
        dec = value
    elif isinstance(value, int):
        dec = _Decimal(value)
    elif isinstance(value, str):
        cleaned = value.strip().replace(",", "")
        if not cleaned:
            raise ValueError("金額字串不得為空")
        if not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", cleaned):
            raise ValueError("金額字串格式不合法")
        dec = _Decimal(cleaned)
    else:
        raise TypeError(
            f"金額不支援的型別: {type(value).__name__}")
    if dec.is_nan() or dec.is_infinite():
        raise ValueError("金額不得為 NaN/Infinity")
    if dec < 0:
        raise ValueError("金額不得為負數")
    return dec


def quantize_twd_for_legacy_display(converted: ConvertedMoney) -> int:
    """P1.1：legacy display 量化（ROUND_HALF_UP）——僅顯示層。"""
    return int(converted.twd_amount.to_integral_value(
        rounding=_ROUND_HALF_UP))


def _detect_currency(info: dict, post: dict) -> Currency:
    """幣別判定（P1）：優先 info.currency（LLM/結構化），再 post.currency。

    明確標記：TWD/NT$/台幣；RMB/CNY/人民幣/¥；USD/美元。
    裸數字無標記 → UNKNOWN（不得猜測、不得默認 TWD）。
    """
    raw = str(info.get("currency") or post.get("currency") or "").strip().upper()
    if raw in ("TWD", "NT$", "NTD", "台幣", "臺幣", "新台幣", "NT"):
        return Currency.TWD
    if raw in ("RMB", "CNY", "人民幣", "¥"):
        return Currency.RMB
    if raw in ("USD", "US$", "美元"):
        return Currency.USD
    return Currency.UNKNOWN


def _expected_rate_for(currency: Currency,
                        svc: CurrencyService) -> _Decimal | None:
    """依 original.currency 計算 expected rate（UNKNOWN → None）。"""
    if currency is Currency.TWD:
        return _Decimal("1")
    if currency is Currency.RMB:
        return svc.rmb_to_twd
    if currency is Currency.USD:
        return svc.usd_to_rmb * svc.rmb_to_twd
    return None


def validate_preconverted_money(
    *,
    converted: object,
    currency_service: CurrencyService,
    expected_original: Money | None,
) -> SellerAskConversionResult | None:
    """P1.2：ConvertedMoney 完整一致性驗證（rate/amount/source/original）。

    通過 → None（可繼續使用）；失敗 → SellerAskConversionResult(valid=False)
    固定錯誤碼。不得接受 subclass（exact-type）。
    """
    if type(converted) is not ConvertedMoney:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_type_invalid")
    cm = converted
    if type(cm.original) is not Money:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_original_mismatch")
    if expected_original is not None and cm.original != expected_original:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_original_mismatch")
    expected_rate = _expected_rate_for(cm.original.currency, currency_service)
    if expected_rate is None:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_unknown_currency")
    if cm.rate_used != expected_rate:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_rate_mismatch")
    expected_amount = cm.original.amount * expected_rate
    if cm.twd_amount != expected_amount:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_amount_mismatch")
    if cm.rate_source != currency_service.rate_source:
        return SellerAskConversionResult(
            None, None, False, "currency_preconverted_source_mismatch")
    return None


# ============================================================
# P6-R1-E1: structured market consumer（真正接管——每 candidate 個別分析）
# ============================================================
def process_structured_market_candidates(
    candidates: list,
    *,
    post: dict,
    currency_service=None,
    lookup_func=None,
    analysis_func=None,
    upload_func=None,
    upload_enabled: bool = False,
) -> tuple[list, list]:
    """每個 eligible structured candidate 個別 conversion → lookup → analysis → deal。

    輸入：
      candidates   — MarketCandidate list（由 build_market_candidates 產生）
      post         — 原始貼文（currency detection 用）
      lookup_func  — market lookup（production default = lookup_buff_price；測試可注入 fake）
      analysis_func— analysis（production default = analyze_arbitrage；測試可注入 fake）
      upload_func  — upload（production default = upload_to_cloud）

    輸出：
      (outcomes, deals)
      outcomes — 每 candidate 的診斷 dict（mh/amount/currency/twd/lookup/blocked）
      deals    — 成功的 analysis 結果

    規則：
      1. 每個 eligible candidate 個別 conversion（resolve_seller_ask_conversion——P1.1 唯一 stage）
      2. 每個 eligible candidate 最多一次 lookup
      3. lookup result 直接供該 candidate 的 analysis
      4. 每成功 candidate 一筆 deal
      5. 不執行 legacy lookup（structured path 啟用時）
      6. 第一個 candidate 失敗不影響其他
      7. seller ask TWD 傳入 analysis context
      8. 不引入 P7 routing / 不修改 P8 profit 邊界
    """
    from alkaid_cs2.domain.market_candidate import eligible_candidates

    outcomes: list[dict] = []
    deals: list[dict] = []
    lookup = lookup_func or lookup_buff_price
    upload = upload_func or upload_to_cloud

    for cand in eligible_candidates(candidates):
        outcome: dict = {
            "item_index": cand.item_index,
            "market_hash_name": cand.market_hash_name,
            "price_image_index": cand.price_image_index,
            "associated_item_index": cand.associated_item_index,
            "blocked": cand.blocked,
            "block_reason": cand.block_reason,
            "lookup_called": False,
            "lookup_result": None,
            "analysis_result": None,
        }
        if cand.blocked or not cand.verified or cand.original_money is None:
            outcomes.append(outcome)
            continue
        # 個別 P1 conversion（原始 Money——P1.2 typed）
        conv = resolve_seller_ask_conversion({"original": cand.original_money}, post,
                                             currency_service=currency_service)
        if not conv.valid:
            outcome["blocked"] = True
            outcome["block_reason"] = "P6_MARKET_CANDIDATE_CONVERSION_INVALID"
            outcome["conversion_error"] = conv.error_code
            outcomes.append(outcome)
            continue
        twd = conv.converted
        outcome["converted_twd"] = twd
        # lookup（每個 candidate 最多一次——結果綁定 candidate identity）
        buff = lookup(cand.market_hash_name)
        outcome["lookup_called"] = True
        outcome["lookup_result"] = buff
        if buff is None:
            outcomes.append(outcome)
            continue
        # ── P6-R1-E2：candidate-specific analysis context（不污染共享 post）──
        # 每個 candidate 用獨立 post copy：seller ask TWD + market identity 綁定
        candidate_post = dict(post)
        candidate_post["_seller_price"] = quantize_twd_for_legacy_display(twd)
        candidate_post["_structured_market_hash_name"] = cand.market_hash_name
        # 預設 analysis：analyze_arbitrage(candidate_post, buff)——使用 candidate context
        if analysis_func is not None:
            deal = analysis_func(cand.market_hash_name, buff, twd)
        else:
            deal = analyze_arbitrage(candidate_post, buff)
        outcome["analysis_result"] = deal
        outcomes.append(outcome)
        if deal:
            deals.append(deal)
            if upload_enabled:
                upload(deal)
    return outcomes, deals


def dispatch_structured_or_legacy(mode, result, post):
    """P6-R1-E3：唯一 structured/legacy dispatch（每 post 呼叫一次）。

    回傳 (dispatch, outcomes, s_deals)：
      "structured" — structured consumer 已執行（caller 處理 report/history/processed_ids/continue）
      "legacy"     — 走 legacy path（off/shadow/safe-no-eligible）
      "fail_closed"— v2_only 無 eligible（caller skip——不 legacy fallback）
    """
    from alkaid_cs2.domain.market_candidate import eligible_candidates

    if mode == "off":
        return "legacy", [], []
    sc = getattr(result, "structured_candidates", None) or []
    eligible = eligible_candidates(sc)
    if mode == "shadow":
        # structured 僅 audit——正式 legacy（無 structured side effect）
        return "legacy", [], []
    if mode == "v2_only":
        if not eligible:
            # fail-closed：即使 legacy data 合法也不 fallback
            return "fail_closed", [], []
        outcomes, deals = process_structured_market_candidates(
            sc, post=post, upload_enabled=True)
        return "structured", outcomes, deals
    # safe
    if eligible:
        outcomes, deals = process_structured_market_candidates(
            sc, post=post, upload_enabled=True)
        return "structured", outcomes, deals
    # safe 無 eligible → 依既有 safe fallback 契約走 legacy
    return "legacy", [], []


def resolve_seller_ask_conversion(
    data: dict,
    post: dict,
    currency_service: CurrencyService | None = None,
) -> SellerAskConversionResult:
    """P1.1：唯一共用換算 stage（mode=off / legacy / shadow / safe / v2 / vision）。

    輸入 data 可含：
    - converted（已合法 ConvertedMoney）→ 直接使用，不再換算
    - original_price/seller_price + currency → 由 CurrencyService 換算一次
    - 兩者皆有 → 驗證一致性（converted.original == original）

    UNKNOWN / 無 currency → fail-closed（price_currency_unresolved）。
    """
    svc = currency_service or _CURRENCY_SERVICE

    # P1.2 輸入優先順序：
    # 1. typed original: Money → 2. original_price + currency → 3. legacy seller_price
    typed_original = data.get("original")
    money: Money | None = None
    if type(typed_original) is Money:
        money = typed_original
        # typed 與 legacy 欄位一致檢查（矛盾 fail-closed）
        raw_legacy = data.get("original_price", data.get("seller_price"))
        if raw_legacy is not None:
            try:
                if parse_original_amount(raw_legacy) != money.amount:
                    return SellerAskConversionResult(
                        None, None, False, "currency_original_field_mismatch")
            except (TypeError, ValueError):
                return SellerAskConversionResult(
                    None, None, False, "currency_original_field_mismatch")
        cur_str = data.get("currency")
        if cur_str is not None and                 str(cur_str).upper() != money.currency.value:
            return SellerAskConversionResult(
                None, None, False, "currency_original_field_mismatch")
    else:
        raw = data.get("original_price", data.get("seller_price", None))
        try:
            amount = parse_original_amount(raw)
        except (TypeError, ValueError):
            return SellerAskConversionResult(
                None, None, False, "price_invalid_amount")
        currency = _detect_currency(data, post)
        if currency is Currency.UNKNOWN:
            # P1.1：fail-closed——不得默認 TWD、不得原值繼續
            return SellerAskConversionResult(
                None, None, False, "price_currency_unresolved")
        try:
            money = Money(amount, currency)
        except (TypeError, ValueError):
            return SellerAskConversionResult(
                None, None, False, "price_invalid_amount")

    # 已換算結果（V2 提供 ConvertedMoney）——先一致性驗證再使用
    converted_in = data.get("converted")
    if converted_in is not None:
        bad = validate_preconverted_money(
            converted=converted_in,
            currency_service=svc,
            expected_original=money,
        )
        if bad is not None:
            return bad
        return SellerAskConversionResult(
            converted_in.original, converted_in, True, None)

    try:
        converted = svc.to_twd(money)
    except (UnsupportedCurrencyError, MoneyValidationError):
        return SellerAskConversionResult(
            None, None, False, "currency_unknown")
    return SellerAskConversionResult(
        money, converted, True, None)


def _get_legacy_validator():
    """延遲初始化 ItemValidator（每 candidate 不重讀 catalog）。"""
    global _LEGACY_VALIDATOR
    if _LEGACY_VALIDATOR is None:
        from alkaid_cs2.services.item_validator import ItemValidator
        _LEGACY_VALIDATOR = ItemValidator()
    return _LEGACY_VALIDATOR


# Phase P2.4：LLM API transport 具體例外（openai SDK，deepseek 相容）
try:
    from openai import (  # noqa: F401
        APIError,
        APIConnectionError,
        APITimeoutError,
        RateLimitError,
    )
    _LLM_TRANSPORT_ERRORS = (
        APIError, APIConnectionError, APITimeoutError, RateLimitError)
except ImportError:
    _LLM_TRANSPORT_ERRORS = ()


def _call_legacy_llm_json(
    client,
    *,
    prompt: str,
    temperature: float,
) -> dict | None:
    """LLM transport + JSON parse 邊界（P2.4）。

    只負責單一 API 呼叫與 parse；回傳 dict 或 None（失敗）。
    只捕捉具體預期例外：JSON parse（JSONDecodeError/TypeError/
    AttributeError）與 SDK transport（_LLM_TRANSPORT_ERRORS）。
    不含任何本地 validation 邏輯。
    """
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=temperature,
            max_tokens=200,
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if not isinstance(content, str) or not content.strip():
            return None
        return json.loads(content)
    except (json.JSONDecodeError, TypeError, AttributeError):
        return None
    except _LLM_TRANSPORT_ERRORS:
        return None


def _apply_validation_result(
    payload: dict,
    result: dict[str, object],
) -> dict:
    """把 ItemValidationResult（dict 化）套用至 payload（P2.4）。

    建立新 dict（不原地修改 caller payload）；verified metadata
    （market_hash_name/verified/verified_by/validation_error/
    validation_attempts）完全來自 result——不接受 caller 提供的
    任何 verified 值。seller_price/confidence 從 payload 保留。
    """
    out = dict(payload)
    out["market_hash_name"] = result.get("market_hash_name")
    verified = result.get("verified")
    if verified is not True:
        verified = False
    out["verified"] = verified
    out["verified_by"] = result.get("verified_by")
    out["validation_error"] = result.get("validation_error")
    out["validation_attempts"] = result.get("attempts")
    return out


def _validate_legacy_candidate(
    market_hash_name: str,
    *,
    source: str,
) -> dict[str, object]:
    """legacy candidate → 同一 ItemValidator canonical 驗證（P2.3）。

    只做 ItemValidationResult → dict 結構轉換；verified metadata
    （verified/verified_by/validation_error/canonical name）完全來自
    ItemValidator，本 helper 不自行宣告任何 verified 值。
    """
    try:
        validator = _get_legacy_validator()
        result = validator.validate_candidate(
            market_hash_name, source=source)
        return {
            "market_hash_name": result.canonical_market_hash_name,
            "verified": result.verified,
            "verified_by": result.verified_by,
            "validation_error": result.validation_error,
            "attempts": result.attempts,
            "original_name": result.original_name,
        }
    except RuntimeError as exc:
        # catalog 不可用 → fail-closed（由 process_posts 邊界捕捉）
        raise RuntimeError("item_validator_catalog_unavailable") from exc


def extract_skin_info(post_text: str) -> dict | None:
    # 先查對照表
    import os, json as _json
    dict_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skin_dict.json")
    if not os.path.exists(dict_path):
        # Phase P2.2：catalog 不可用 → fail-closed（不得落入 LLM）
        raise RuntimeError("item_validator_catalog_unavailable")
    try:
        with open(dict_path, "r", encoding="utf-8") as f:
            dict_data = _json.load(f)
        skin_dict = dict_data.get("pattern_cn_to_en", {})
        full_dict = dict_data.get("full_cn_to_en", {})

        # ── 第一優先: 完整名稱直接對照 ──
        # 例如 Vision 讀到「沙漠之鹰 | 东方之谜」→ 直接對「Desert Eagle | Eastern Enigma」
        for cn_full, en_full in full_dict.items():
            if cn_full in post_text:
                    print(f"  [字典] ✅ 完整名命中: {cn_full} → {en_full}")
                    # 判斷磨損度 (簡繁相反陷阱已處理)
                    wear_en = _wear_to_en(post_text)
                    # 暗金
                    is_st = "暗金" in post_text or "StatTrak" in post_text
                    full_name = en_full
                    if is_st and "StatTrak" not in full_name:
                        full_name = full_name.replace("★ ", "★ StatTrak™ ", 1)
                        if "★ " not in full_name:
                            full_name = "StatTrak™ " + full_name
                    full_name = f"{full_name} ({wear_en})"
                    print(f"  [字典] 🛠️ 組裝: {full_name}")
                    # 價格
                    import re
                    price = -1
                    text_clean = post_text.replace(',', '')
                    m_eq = re.search(r'=\s*(\d[\d,]*)\s*(?:NT|TWD)?', text_clean)
                    if m_eq:
                        price = int(m_eq.group(1))
                    else:
                        candidates = re.findall(r'(?<![\d.])(\d{3,})(?:NT|TWD|\$)?', text_clean)
                        if candidates:
                            price = int(candidates[-1])
                    # Phase P2.2：dictionary 命中只是 candidate evidence，
                    # verified 必須經同一 ItemValidator canonical 驗證
                    vres = _validate_legacy_candidate(
                        full_name, source="legacy_dict_full")
                    return {"market_hash_name": vres["market_hash_name"],
                            "seller_price": price, "confidence": "high",
                            "verified": vres["verified"],
                            "verified_by": vres["verified_by"],
                            "validation_error": vres["validation_error"]}

            # ── 第二優先: 花紋對照 + 武器拼裝 ──
            # 武器中英對照（保留武器前綴用）
            weapon_map = {
                "AK-47": "AK-47", "ak": "AK-47", "阿卡": "AK-47",
                "M4A1-S": "M4A1-S", "M4A4": "M4A4", "沙鹰": "Desert Eagle", "沙鷹": "Desert Eagle",
                "蝴蝶刀": "Butterfly Knife", "爪子刀": "Karambit", "爪刀": "Karambit",
                "刺刀": "Bayonet", "折刀": "Flip Knife", "锯齿爪刀": "Huntsman Knife",
                "锯齿爪刀": "Huntsman Knife", "穿肠刀": "Gut Knife", "猎刀": "Huntsman Knife",
                "猎杀者": "Huntsman Knife", "系绳者": "Talon Knife", "穿刺者": "Stiletto Knife",
                "求生匕首": "Survival Knife", "流浪者": "Nomad Knife", "骷髅匕首": "Skeleton Knife",
                "运动手套": "Sport Gloves", "运动手套": "Sport Gloves", "專業手套": "Specialist Gloves",
                "专业手套": "Specialist Gloves", "驾驶手套": "Driver Gloves", "駕駛手套": "Driver Gloves",
                "摩托手套": "Moto Gloves", "手部束带": "Hand Wraps", "手部束帶": "Hand Wraps",
                "血猎手套": "Bloodhound Gloves", "血獵手套": "Bloodhound Gloves",
                "运动手套": "Sport Gloves", "裹手": "Hand Wraps",
                "沙漠之鹰": "Desert Eagle", "沙鹰": "Desert Eagle", "沙鷹": "Desert Eagle",
            }

            # 比對貼文中是否包含對照表中的中文關鍵字
            for cn, en in skin_dict.items():
                if cn in post_text and len(cn) >= 2:
                    print(f"  [字典] ✅ 查表命中: {cn} → {en}")
                    # 判斷磨損度 (簡繁相反陷阱已處理)
                    wear_en = _wear_to_en(post_text)

                    # 判斷武器前綴（從貼文中找武器關鍵字）
                    weapon_en = ""
                    for cn_w, en_w in weapon_map.items():
                        if cn_w in post_text:
                            weapon_en = en_w
                            break
                    # 暗金判斷
                    is_stattrak = "暗金" in post_text or "StatTrak" in post_text

                    # 組裝完整名稱: [★] [StatTrak™] 武器 | 花紋 (磨損)
                    prefix = ""
                    if weapon_en in ("Butterfly Knife", "Karambit", "Bayonet", "Flip Knife",
                                     "Huntsman Knife", "Gut Knife", "Sport Gloves", "Specialist Gloves",
                                     "Driver Gloves", "Moto Gloves", "Hand Wraps", "Bloodhound Gloves"):
                        prefix = "★ "
                    if is_stattrak:
                        prefix += "StatTrak™ "
                    if weapon_en:
                        full_name = f"{prefix}{weapon_en} | {en} ({wear_en})"
                    else:
                        full_name = f"{prefix}{en} ({wear_en})"
                    print(f"  [字典] 🛠️ 組裝: {full_name}")

                    # 判斷價格 — 優先抓「算」後的最終價(如 =5412算5000 → 5000)
                    import re
                    price = -1
                    text_clean = post_text.replace(',', '')
                    # 「算5000」「算你5000」「去尾算5000」→ 5000
                    m_suan = re.search(r'算(?:你|給|到)?\s*(\d[\d,]*)', text_clean)
                    if m_suan:
                        price = int(m_suan.group(1))
                    else:
                        # 其次「=」後面的結果(如 同磨底2100*4.4=9200 → 9200)
                        m_eq = re.search(r'=\s*(\d[\d,]*)\s*(?:NT|TWD)?', text_clean)
                        if m_eq:
                            price = int(m_eq.group(1))
                        else:
                            # 最後抓最像賣價的數字
                            candidates = re.findall(r'(?<![\d.])(\d{3,})(?:NT|TWD|\$)?', text_clean)
                            if candidates:
                                price = int(candidates[-1])
                    # Phase P2.2：pattern 命中只是 candidate evidence；
                    # 組裝名稱經同一 ItemValidator canonical 驗證
                    vres = _validate_legacy_candidate(
                        full_name, source="legacy_dict_pattern")
                    return {"market_hash_name": vres["market_hash_name"],
                            "seller_price": price, "confidence": "high",
                            "verified": vres["verified"],
                            "verified_by": vres["verified_by"],
                            "validation_error": vres["validation_error"]}
    except (OSError, _json.JSONDecodeError) as exc:
        # Phase P2.2：catalog 讀取失敗 → fail-closed（不得靜默落入 LLM）
        raise RuntimeError("item_validator_catalog_unavailable") from exc

    client = create_client()
    if not client:
        return None

    prompt = f"""你是一個 CS2 皮膚識別專家.請從以下 FB 社團買賣貼文中,提取**完整精確**的 CS2 皮膚英文名稱.

=== 命名規則(非常重要)===
- 手套類:★ Driver Gloves | King Snake (Field-Tested)
- 刀類:★ Butterfly Knife | Fade (Factory New)
- 槍類:AK-47 | Fire Serpent (Minimal Wear)
- 手套=Gloves, 刀=Knife, 爪=Karambit/Knife, 蝴蝶=Butterfly Knife
- 折刀=Flip Knife, 穿刺者=Stiletto, 系繩者=Talon, 求生=Survival
- 邁阿密=Sport Gloves | Vice(手套),不是King Snake也不是步槍
- 夜行衣=Sport Gloves | Nocts(手套),不是Hand Wraps
- 深紅之網=Crimson Web, 漸層/漸變=Fade
- 淬火=Case Hardened, 自動化=Mecha Industries
- 印花集=Printstream, 無上之焰=Wildfire
- 多普勒=Doppler, 伽瑪多普勒=Gamma Doppler
- 大理石=Fade/Marble Fade, 偽冰火=Fake Fire&Ice
- 底=最低價, 同磨=同磨損區間
- **同磨底=BUFF上同磨損區間的最低價**,貼文常寫「同磨底X*4.4」= BUFF最低價乘匯率
- 如「同磨底 2100*4.4=9200」代表 BUFF 同磨損最低價 2100 RMB × 4.4 匯率 = 賣 9200 TWD
- 底4.3/底4.4=和同磨底同意思,「底4.4」= BUFF同磨損最低價×4.4匯率
- **去尾/抹零=無條件向下取整**,如 9246 去尾算 9200
- **CD貨=冷卻中的物品**,買了要等CD結束才能交易,通常較便宜但有風險
- **磨損度官方對照(簡繁相反陷阱!)**: Factory New=嶄新出廠/崭新出厂/厂新/全新, Minimal Wear=輕微磨損/略有磨损, Field-Tested=久經沙場/久经沙场, **Well-Worn=戰痕累累(繁)/破损不堪(簡)**, **Battle-Scarred=破損不堪(繁)/战痕累累(簡)**
- **手套和刀類名稱前面必須加 ★**(如 ★ Sport Gloves | Vice),槍類不加
- **暗金(StatTrak™)辨識**:名稱前有 StatTrak™ 或貼文提到「暗金」= 暗金武器
- 暗金名稱格式: StatTrak™ AK-47 | Redline (Field-Tested) / ★ StatTrak™ Butterfly Knife | Fade (Factory New)
- 暗金武器價格通常比普通版貴,必須正確標記

=== 貼文內容 ===
{post_text}

=== 請提取 ===
1. market_hash_name:CS2 皮膚完整英文名稱(含磨損),如 "★ Driver Gloves | King Snake (Field-Tested)"
   如果不是 CS2 皮膚買賣請回傳 "NONE".
2. price:賣家開價的**原始數字**,如 2100（不要換算、不要乘匯率）,無價格請回 -1
3. currency:原始幣別,只可為 TWD / RMB / USD / UNKNOWN
   **嚴禁任何匯率換算**——不要乘 4.5、不要換算成台幣、不要更改幣別,
   貼文寫多少就回多少;無法判斷幣別回 UNKNOWN
4. confidence:信心程度(high / medium / low)

只回傳 JSON:{{"market_hash_name":"...","price":2100,"currency":"RMB","confidence":"high"}}"""
    # Phase P2.4：初次 LLM 呼叫（transport + parse 邊界在 helper 內）
    data = _call_legacy_llm_json(client, prompt=prompt, temperature=0.1)
    if data is None:
        # transport / parse 失敗 → 安全 unresolved（fail-closed）
        return {
            "market_hash_name": None,
            "verified": False,
            "verified_by": None,
            "validation_error": "item_validation_service_unavailable",
            "seller_price": -1,
            "confidence": "low",
        }
    if data.get("market_hash_name") == "NONE":
        return None

    # Phase P1：LLM 只回傳原始 price + 原始 currency（不得換算）；
    # seller_price 欄位保留為 original amount 語意（legacy 相容）
    _price_raw = data.get("price", data.get("seller_price", -1))
    data["seller_price"] = _price_raw
    data["original_price"] = _price_raw
    data["currency"] = data.get("currency", "UNKNOWN")
    data.pop("price", None)

    # 自動驗證皮膚名稱是否存在於 csgoskins.gg（transport try 外）
    mhn = data.get("market_hash_name", "")
    if mhn and _verify_skin_on_csgoskins(mhn):
        # Phase P2.4：外部驗證成功仍須本地 ItemValidator；metadata 完整
        # 透傳 ItemValidationResult（不重建 verified_by）
        vres = _validate_legacy_candidate(mhn, source="legacy_llm")
        if vres["verified"] is True:
            out = _apply_validation_result(data, vres)
            return out
        print(f"  [驗證] ⚠️ '{mhn}' 不在本地 canonical catalog")

    # 驗證失敗 → 重試一次（transport + parse 邊界在 helper 內）
    print(f"  [驗證] ⚠️ '{mhn}' 不存在,重新翻譯...")
    retry_prompt = f"""CS2 皮膚名稱翻譯錯誤.請重新翻譯.

原貼文: {post_text}
上次給的: {mhn} ← 這不存在於 csgoskins.gg

請重新給一個正確的英文 market_hash_name(含磨損).
回答 JSON: {{"market_hash_name":"...","seller_price":0,"confidence":"medium"}}"""
    data2 = _call_legacy_llm_json(client, prompt=retry_prompt,
                                  temperature=0.3)
    # 本地 validation 在 transport try 外——programming errors 不得吞掉
    if data2 and data2.get("market_hash_name") and data2["market_hash_name"] != "NONE":
        mhn2 = data2["market_hash_name"]
        if _verify_skin_on_csgoskins(mhn2):
            data2["seller_price"] = data.get("seller_price", data2.get("seller_price", -1))
            # Phase P2.4：retry metadata 完整透傳（同一 helper）
            vres2 = _validate_legacy_candidate(mhn2, source="legacy_llm_retry")
            if vres2["verified"] is True:
                out2 = _apply_validation_result(data2, vres2)
                print(f"  [驗證] ✅ 重試成功: {mhn2}")
                return out2
            print(f"  [驗證] ❌ 重試名稱不在本地 catalog: {mhn2}")
        else:
            print(f"  [驗證] ❌ 重試仍失敗: {mhn2}")
    # Phase P2：驗證兩次失敗不得回傳第一次未驗證名稱（原 L594 缺陷）
    # → 回傳結構化 unresolved 結果（market lookup gate 會阻擋）
    return {
        "market_hash_name": None,
        "verified": False,
        "verified_by": None,
        "validation_error": "item_validation_retry_failed",
        "seller_price": data.get("seller_price", -1),
        "confidence": "low",
    }


def _verify_skin_on_csgoskins(market_hash_name: str) -> bool:
    """驗證皮膚名稱是否存在於 csgoskins.gg (自動校正翻譯錯誤)"""
    import re, time
    slug = market_hash_name.lower()
    slug = slug.replace("'", "").replace("(", "").replace(")", "")
    slug = slug.replace("★ ", "").replace(" | ", "-").replace(" ", "-")
    slug = re.sub(r'-(fn|mw|ft|ww|bs)$', '', slug)
    slug = re.sub(r'-(factory-new|minimal-wear|field-tested|well-worn|battle-scarred)$', '', slug)
    slug = slug.strip('-')
    
    url = f"https://csgoskins.gg/items/{slug}"
    try:
        import urllib.request
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        resp = urllib.request.urlopen(req, timeout=8)
        html = resp.read().decode('utf-8', errors='ignore')
        # 確認不是 404
        if "Page Not Found" not in html and "does not exist" not in html[:500]:
            return True
    except Exception:
        pass
    return False


# ============================================================
# 本地資料庫查詢
# ============================================================

def lookup_buff_price(market_hash_name: str) -> dict | None:
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT price_twd, volume, last_updated FROM buff_prices WHERE market_hash_name = ?",
                (market_hash_name,),
            )
            row = cursor.fetchone()
            if not row:
                cursor.execute(
                    "SELECT market_hash_name, price_twd, volume, last_updated "
                    "FROM buff_prices WHERE market_hash_name LIKE ? LIMIT 1",
                    (f"%{market_hash_name}%",),
                )
                row = cursor.fetchone()
                if row:
                    print(f"  [DB] 模糊匹配 → {row[0]}")
            if row:
                is_fuzzy = len(row) == 4
                return {
                    "market_hash_name": row[0] if is_fuzzy else market_hash_name,
                    "price_twd": float(row[1] if is_fuzzy else row[0]),
                    "volume": int(row[2] if is_fuzzy else row[1]),
                    "last_updated": row[3] if is_fuzzy else row[2],
                }
        finally:
            conn.close()

    # 資料庫查不到 → 透過 csgoskins.gg 查 BUFF 價格
    print(f"  [csgoskins] 資料庫無此皮膚,查詢中...")
    try:
        # 用 subprocess 確保每次都是最新程式
        import subprocess, json as _json
        code = f"import sys; sys.path.insert(0, {_json.dumps(BASE_DIR)}); import csgoskins_bridge; r = csgoskins_bridge.fetch_buff_price({_json.dumps(market_hash_name)}); import json; print(json.dumps(r) if r else 'null')"
        r = subprocess.run(
            ["python", "-c", code],
            capture_output=True, text=True, timeout=30,
        )
        out = r.stdout.strip()
        if out and out != 'null':
            data = _json.loads(out)
            twd = data["price_twd"]
            rmb = data["price_rmb"]
            print(f"  [csgoskins] ✅ {market_hash_name} → ¥{rmb:,.2f} → NT${twd:,.0f}")
            _save_buff_price_to_db(market_hash_name, twd, 0)
            return {
                "market_hash_name": market_hash_name,
                "price_twd": twd,
                "volume": 0,
                "last_updated": time.strftime("%Y-%m-%d %H:%M"),
            }
        else:
            print(f"  [csgoskins] ⚠️  查無價格(stderr: {r.stderr.strip()[:100]})")
    except Exception as e:
        print(f"  [csgoskins] ❌ {e}")

    return None


def _save_buff_price_to_db(name: str, price_twd: float, volume: int):
    """將即時查到的價格寫入資料庫."""
    try:
        import sqlite3, time
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            "INSERT OR REPLACE INTO buff_prices "
            "(market_hash_name, price_twd, volume, last_updated) "
            "VALUES (?, ?, ?, ?)",
            (name, price_twd, volume,
             time.strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
        conn.close()
        print(f"  [DB] ✅ 已寫入資料庫")
    except Exception as e:
        print(f"  [DB] ⚠️  寫入失敗:{e}")


# ============================================================
# DeepSeek 套利分析
# ============================================================

def analyze_arbitrage(post: dict, buff_info: dict) -> dict | None:
    client = create_client()
    if not client:
        return None

    bp = buff_info["price_twd"]
    sp = post.get("_seller_price", 0)
    net = bp * BUFF_FEE_RATE
    profit = net - sp
    margin = (profit / sp * 100) if sp > 0 else 0

    prompt = f"""你是一個 CS2 搬磚套利分析師.請分析以下交易.

## FB 貼文
{post['content']}

## BUFF 基準
- 皮膚:{buff_info['market_hash_name']}
- BUFF 價:NT${bp:,.0f},扣手續費實收 NT${net:,.0f}
- 賣家開價:NT${sp:,.0f}
- 預估利潤:NT${profit:,.0f}({margin:.1f}%)
- 24h 成交量:{buff_info['volume']:,} 件

## 判斷
1. 利潤 > 5%?
2. 有低磨/好印花/稀有模板等額外溢價?
3. 成交量 > 50 件/日?

## 輸出
具潛力 → {{"verdict":"profitable","reason":"50字內原因","risk":"low/medium/high","estimated_profit_twd":{profit:.0f}}}
無潛力 → {{"verdict":"skip","reason":"30字內原因"}}
只回傳 JSON.
"""
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.3, max_tokens=300,
            response_format={"type": "json_object"},
        )
        result = json.loads(resp.choices[0].message.content)
        if result.get("verdict") == "profitable":
            return {
                "post_id": post.get("id", ""),
                "author": post.get("author", "未知"),
                "skin_name": buff_info["market_hash_name"],
                "link": post.get("link", ""),
                "buff_price": bp,
                "seller_price": sp,
                "profit": result.get("estimated_profit_twd", profit),
                "profit_margin": margin,
                "volume": buff_info["volume"],
                "reason": result.get("reason", ""),
                "risk": result.get("risk", "medium"),
            }
        else:
            print(f"  [AI] 跳過:{result.get('reason', '')}")
            return None
    except Exception as e:
        print(f"  [錯誤] DeepSeek 分析失敗:{e}")
        return None


# ============================================================
# 雲端同步存根(預留 Supabase 接口)
# ============================================================

def upload_to_cloud(deal: dict) -> bool:
    """將套利機會上傳至雲端資料庫(Supabase).

    目前為存根函式,日後對接 Supabase 時實作.
    預計寫入欄位:skin_name, buff_price, seller_price, profit,
                   profit_margin, volume, risk, fb_link, reason, created_at

    Args:
        deal: 套利分析結果 dict.

    Returns:
        成功 True / 失敗 False.
    """
    _ = deal  # 預留
    # TODO: 對接 Supabase REST API
    # TODO: 寫入 supabase_picks 資料表
    return True  # 靜默成功,不影響主流程


def save_deal_to_history(deal: dict):
    """將套利結果寫入 deals_history.json(供 Streamlit 讀取)."""
    history = []
    if os.path.exists(DEALS_HISTORY_PATH):
        try:
            with open(DEALS_HISTORY_PATH, "r", encoding="utf-8") as f:
                history = json.load(f)
        except (json.JSONDecodeError, IOError):
            history = []

    now = datetime.now()
    deal["created_at"] = now.strftime("%Y-%m-%d %H:%M:%S")

    # 插到最前面(最新在上面),最多保留 N 筆
    history.insert(0, deal)
    history = history[:MAX_DEALS_HISTORY]

    with open(DEALS_HISTORY_PATH, "w", encoding="utf-8") as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def get_recent_deals(limit: int = 20) -> list[dict]:
    """讀取最近 N 筆套利結果(供 Streamlit 呼叫)."""
    if not os.path.exists(DEALS_HISTORY_PATH):
        return []
    try:
        with open(DEALS_HISTORY_PATH, "r", encoding="utf-8") as f:
            return json.load(f)[:limit]
    except (json.JSONDecodeError, IOError):
        return []


# ============================================================
# 結果輸出
# ============================================================

def print_deal_report(deal: dict):
    print()
    print("=" * 60)
    print("  ✅ 發現套利機會！")
    print("=" * 60)
    print(f"  物品:{deal['skin_name']}")
    print(f"  賣家:{deal['author']}")
    print(f"  連結:{deal['link']}")
    print(f"  ─────────────────────────────")
    print(f"  賣家開價:    NT${deal['seller_price']:>8,.0f}")
    print(f"  BUFF 基準價: NT${deal['buff_price']:>8,.0f}")
    print(f"  預估利潤:    NT${deal['profit']:>8,.0f}  ({deal['profit_margin']:.1f}%)")
    print(f"  BUFF 成交量:  {deal['volume']:>6,} 件/日")
    print(f"  風險等級:    {deal['risk'].upper()}")
    print(f"  AI 分析:     {deal['reason']}")
    print("=" * 60)


# ============================================================
# 主流程
# ============================================================

def extract_vision_inputs_from_post(post: dict) -> list | None:
    """從 post 整理 VisionImageInput 清單（無資料 → None；非法資料 warning 不 crash）。

    來源優先順序：
    A. post["vision_inputs"]
    B. post["vision_payloads"]（image_url 從 post["images"] 對應）
    C. post["items"]（舊 crawler Vision 結果 → 單一 payload）
    D. 無資料 → None
    """
    from alkaid_cs2.integration.vision_production import VisionImageInput

    import copy

    results: list = []
    post_id = post.get("id", "")
    images = post.get("images") or []

    # A. vision_inputs
    vis = post.get("vision_inputs")
    if vis is not None:
        for i, item in enumerate(vis):
            try:
                if isinstance(item, VisionImageInput):
                    # defensive copy：不共享呼叫端物件參照
                    results.append(VisionImageInput(
                        image_index=item.image_index,
                        image_url=item.image_url,
                        image_hash=item.image_hash,
                        payload=copy.deepcopy(item.payload),
                    ))
                elif isinstance(item, dict):
                    results.append(VisionImageInput(
                        image_index=int(item.get("image_index", i)),
                        image_url=item.get("image_url", ""),
                        image_hash=item.get("image_hash"),
                        payload=item.get("payload"),
                    ))
                else:
                    print(f"    ⚠️ vision_inputs[{i}] 格式不支援,跳過")
            except (TypeError, ValueError) as exc:
                print(f"    ⚠️ vision_inputs[{i}] 無效: {exc}")
        return results or None

    # B. vision_payloads
    vps = post.get("vision_payloads")
    if vps is not None:
        if isinstance(vps, list):
            for i, payload in enumerate(vps):
                url = images[i] if i < len(images) else f"inline://post/{post_id}/image/{i}"
                try:
                    results.append(VisionImageInput(image_index=i, image_url=url, payload=payload))
                except (TypeError, ValueError) as exc:
                    print(f"    ⚠️ vision_payloads[{i}] 無效: {exc}")
            return results or None
        print("    ⚠️ vision_payloads 不是 list,忽略")
        return None

    # C. 舊 post["items"]（crawler Vision 結果）
    items = post.get("items")
    if items:
        payload = {
            "type": "multi" if len(items) > 1 else "single",
            "platform": "facebook",
            "items": items,
        }
        url = images[0] if images else f"inline://post/{post_id}/image/0"
        try:
            return [VisionImageInput(image_index=0, image_url=url, payload=payload)]
        except (TypeError, ValueError) as exc:
            print(f"    ⚠️ post items 轉換無效: {exc}")
            return None

    # D. 無資料
    return None


def process_posts(posts: list[dict]) -> list[dict]:
    deals = []
    processed_ids = []

    for i, post in enumerate(posts, 1):
        print(f"\n{'─' * 50}")
        print(f"[{i}/{len(posts)}] {post.get('author', '未知')}")

        # Step 1（V2 受控整合：off 完全 legacy，其餘模式走 production_bridge）
        from alkaid_cs2.integration.production_bridge import (
            _METRICS,
            get_v2_parser_mode,
            is_valid_legacy_seller_price,
            parse_post_for_production,
        )
        mode = get_v2_parser_mode()
        if mode == "off":
            print("  [1/3] DeepSeek 提取皮膚...")
            try:
                info = extract_skin_info(post.get("content", ""))
            except RuntimeError as exc:
                # Phase P2：validator service unavailable → fail-closed
                print(f"  [1/3] ⛔ 驗證服務不可用, skip: {exc}")
                processed_ids.append(post.get("id", ""))
                continue
            if info is None:
                processed_ids.append(post.get("id", ""))
                continue
            mh, sp, conf = info.get("market_hash_name", ""), info.get("seller_price", -1), info.get("confidence", "low")
            # Phase P1.1：共用換算 stage（UNKNOWN → fail-closed）
            conv = resolve_seller_ask_conversion(info, post)
            if not conv.valid:
                print(f"  [1/3] ⛔ 換算失敗 "
                      f"({conv.error_code}), skip")
                processed_ids.append(post.get("id", ""))
                continue
            sp = quantize_twd_for_legacy_display(conv.converted)
            if sp <= 0:
                print("  [1/3] ⚠️ 無價格,跳過")
                processed_ids.append(post.get("id", ""))
                continue
            # Phase P2：market lookup 最後防線（mode=off 亦作用）
            verified_item = require_verified_market_item(info)
            if verified_item is None:
                print("  [2/3] ⛔ P2 gate: 未驗證商品 blocked "
                      f"({info.get('validation_error', 'no_verified_flag')})")
                processed_ids.append(post.get("id", ""))
                continue
            mh = verified_item.market_hash_name
            print(f"  [1/3] ✅ {mh} | NT${sp:,.0f} | {conf}")
            post["_seller_price"] = sp
        else:
            full_v2, pattern_v2 = _load_v2_dicts()
            vision_inputs = extract_vision_inputs_from_post(post)
            post_link = post.get("link") or post.get("url") or ""
            result = parse_post_for_production(
                post_id=post.get("id", ""),
                author=post.get("author", ""),
                link=post_link,
                post_text=post.get("content", ""),
                image_urls=post.get("images", []) or [],
                vision_inputs=vision_inputs,
                full_dict=full_v2,
                pattern_dict=pattern_v2,
                weapon_map=_V2_WEAPON_MAP,
                legacy_parser=extract_skin_info,
                mode=mode,
            )
            _METRICS.record(result)
            # P6-R1-E4：blocked 優先 fail-closed；structured dispatch 不依賴 legacy data 存在
            if result.blocked:
                print(f"  [1/3] ⏭️ skipped ({result.source})")
                processed_ids.append(post.get("id", ""))
                continue

            # ── P6-R1-E3: 唯一 structured/legacy dispatch ──
            # structured consumer invocation 每 post 最多 1 次；
            # v2_only 無 eligible → fail-closed；shadow → 正式 legacy（無 structured side effect）；
            # structured path 不寫入原始 post（candidate_post copy 各自取得價格）。
            dispatch, outcomes, s_deals = dispatch_structured_or_legacy(mode, result, post)
            if dispatch == "fail_closed":
                print("  [1/3] ⛔ v2_only fail-closed（無 eligible structured candidate）")
                processed_ids.append(post.get("id", ""))
                continue
            if dispatch == "structured":
                for oc in outcomes:
                    if oc.get("blocked"):
                        print(f"  [1/3] ⛔ [structured] blocked "
                              f"({oc.get('block_reason')}) {oc.get('market_hash_name')}")
                    elif oc.get("lookup_result") is None:
                        print(f"  [2/3] ⚠️ [structured] 無此皮膚 "
                              f"{oc.get('market_hash_name')}")
                    else:
                        _twd = oc.get("converted_twd")
                        _amt = getattr(_twd, "twd_amount", _twd)
                        print(f"  [2/3] ✅ [structured] "
                              f"{oc.get('market_hash_name')} | NT${_amt:,.0f} | "
                              f"img_idx={oc.get('price_image_index')}")
                for deal in s_deals:
                    deals.append(deal)
                    print_deal_report(deal)
                    save_deal_to_history(deal)
                processed_ids.append(post.get("id", ""))
                continue  # structured path 完成——不執行 legacy gates/lookup
            # legacy（off/shadow/safe-no-eligible）——此時才要求 legacy data
            if result.data is None:
                print(f"  [1/3] ⏭️ skipped（legacy 無 data——{result.source}）")
                processed_ids.append(post.get("id", ""))
                continue

            data = result.data
            mh = data.get("market_hash_name", "")
            sp = data.get("seller_price", -1)
            conf = data.get("confidence", "low")
            # Phase P1.1：V2/legacy/shadow/safe/vision 共用同一換算 stage
            conv = resolve_seller_ask_conversion(data, post)
            if not conv.valid:
                print(f"  [1/3] ⛔ 換算失敗 "
                      f"({conv.error_code}), skip")
                processed_ids.append(post.get("id", ""))
                continue
            sp = quantize_twd_for_legacy_display(conv.converted)
            if not is_valid_legacy_seller_price(sp):
                print("  [1/3] ⚠️ 無有效價格,跳過")
                processed_ids.append(post.get("id", ""))
                continue
            # Phase P2：market lookup 最後防線（mode=off 亦作用）
            verified_item = require_verified_market_item(data)
            if verified_item is None:
                print("  [2/3] ⛔ P2 gate: 未驗證商品 blocked "
                      f"({data.get('validation_error', 'no_verified_flag')})")
                processed_ids.append(post.get("id", ""))
                continue
            mh = verified_item.market_hash_name
            print(f"  [1/3] ✅ [{result.source}] {mh} | NT${sp:,.0f} | {conf}")
            post["_seller_price"] = sp

        # Step 2
        print("  [2/3] 查詢 BUFF 價格...")
        buff = lookup_buff_price(mh)
        if buff is None:
            print(f"  [2/3] ⚠️ 資料庫無此皮膚")
            processed_ids.append(post.get("id", ""))
            continue
        print(f"  [2/3] ✅ NT${buff['price_twd']:,.0f} | 成交量 {buff['volume']:,}")

        # Step 3
        print("  [3/3] DeepSeek 套利分析...")
        deal = analyze_arbitrage(post, buff)
        if deal:
            deals.append(deal)
            print_deal_report(deal)
            # 同步上傳至雲端(靜默執行)
            upload_to_cloud(deal)
            save_deal_to_history(deal)
        else:
            print("  [3/3] ➖ 無套利空間")

        processed_ids.append(post.get("id", ""))

    # 標記已處理
    state = load_state()
    mark_processed(processed_ids, state)
    save_state(state)
    return deals


def is_operating_hours() -> bool:
    now = datetime.now().time()
    return dtime(8, 0) <= now <= dtime(23, 0)


def main():
    print("=" * 60)
    print("  CS2 搬磚套利分析機器人 (v2 - Playwright)")
    print(f"  API:{API_BASE} | 模型:{MODEL}")
    print(f"  來源:{'Playwright ' + str(FB_GROUP_URLS) if FB_GROUP_URLS else '模擬貼文'}")
    print(f"  排程:08:00-23:00 每 10 分鐘")
    print("=" * 60)

    if not API_KEY:
        print("\n❌ export DEEPSEEK_API_KEY=sk-...")
        return
    if not os.path.exists(DB_PATH):
        print("\n⚠️ 請先跑 fetch_prices.py")
        return

    # 測試 DeepSeek
    print("\n🔌 測試 DeepSeek API...")
    c = create_client()
    if not c:
        return
    try:
        c.chat.completions.create(model=MODEL, messages=[{"role": "user", "content": "ping"}], max_tokens=5)
        print("   ✅ 連線成功")
    except Exception as e:
        print(f"   ❌ {e}")
        return

    # 主循環
    cycle = 0
    while True:
        cycle += 1
        now = datetime.now()
        print(f"\n{'#' * 60}")
        print(f"  🔄 # {cycle} - {now.strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'#' * 60}")

        if not is_operating_hours():
            wait = (8 - now.hour - 1) * 60 + (60 - now.minute) if now.hour >= 23 else (8 - now.hour) * 60 - now.minute
            print(f"🌙 休眠中,{wait:.0f} 分後再檢查")
            time.sleep(min(wait * 60, 600))
            continue

        # 抓貼文(Playwright 或模擬)
        raw = fetch_fb_posts()
        state = load_state()
        posts = filter_new_posts(raw, state)
        print(f"   新貼文:{len(posts)}/{len(raw)} 篇")

        if posts:
            deals = process_posts(posts)
            print(f"\n📊 結果:{len(deals)} 個套利機會")
            for d in deals:
                print(f"  ✅ {d['skin_name']} → NT${d['profit']:,.0f}")
        else:
            print("📭 無新貼文")

        state = load_state()
        state["last_fetch"] = now.strftime("%Y-%m-%d %H:%M:%S")
        save_state(state)

        print("\n⏳ 10 分鐘後下一輪...")
        time.sleep(600)


if __name__ == "__main__":
    main()
