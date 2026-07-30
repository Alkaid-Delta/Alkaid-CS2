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


def extract_skin_info(post_text: str) -> dict | None:
    client = create_client()
    if not client:
        return None

    prompt = f"""你是一個 CS2 皮膚識別專家.請從以下 FB 社團買賣貼文中,提取**完整精確**的 CS2 皮膚英文名稱.

=== 命名規則(非常重要)===
- 手套類:★ Driver Gloves | King Snake (Field-Tested)
- 刀類:★ Butterfly Knife | Fade (Factory New)
- 槍類:AK-47 | Fire Serpent (Minimal Wear)
- 注意「邁阿密」= 「★ Driver Gloves | King Snake」,不是步槍
- 注意「夜行衣」一定是手套或刀皮膚
- 注意「漸層/漸變」通常是 Fade
- 「淬火」= Case Hardened

=== 貼文內容 ===
{post_text}

=== 請提取 ===
1. market_hash_name:CS2 皮膚完整英文名稱(含磨損),如 "★ Driver Gloves | King Snake (Field-Tested)"
   如果不是 CS2 皮膚買賣請回傳 "NONE".
2. seller_price:賣家開價(新台幣 TWD),無價格請回 -1
3. confidence:信心程度(high / medium / low)

只回傳 JSON:{{"market_hash_name":"...","seller_price":0,"confidence":"high"}}"""
    try:
        resp = client.chat.completions.create(
            model=MODEL, messages=[{"role": "user", "content": prompt}],
            temperature=0.1, max_tokens=200,
            response_format={"type": "json_object"},
        )
        data = json.loads(resp.choices[0].message.content)
        if data.get("market_hash_name") == "NONE":
            return None
        return data
    except Exception as e:
        print(f"  [錯誤] DeepSeek 提取失敗:{e}")
        return None


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

def process_posts(posts: list[dict]) -> list[dict]:
    deals = []
    processed_ids = []

    for i, post in enumerate(posts, 1):
        print(f"\n{'─' * 50}")
        print(f"[{i}/{len(posts)}] {post.get('author', '未知')}")

        # Step 1
        print("  [1/3] DeepSeek 提取皮膚...")
        info = extract_skin_info(post.get("content", ""))
        if info is None:
            processed_ids.append(post.get("id", ""))
            continue
        mh, sp, conf = info.get("market_hash_name", ""), info.get("seller_price", -1), info.get("confidence", "low")
        if sp <= 0:
            print("  [1/3] ⚠️ 無價格,跳過")
            processed_ids.append(post.get("id", ""))
            continue
        print(f"  [1/3] ✅ {mh} | NT${sp:,.0f} | {conf}")
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
