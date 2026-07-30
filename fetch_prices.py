"""
fetch_prices.py — CS2 皮膚價格爬取腳本 (BUFF 163)
===================================================
透過 BUFF 163 官方 API 查詢熱門搬磚皮膚的價格，
提取 sell_min_price（RMB）與交易量，
自動轉換為新台幣（TWD）後寫入 SQLite 資料庫。

使用方法：
   1. 編輯同目錄下的 config.txt，貼上 BUFF Cookie
   2. 執行：python fetch_prices.py

Cookie 過期時，只需：
   1. 重新登入 https://buff.163.com
   2. 複製新 Cookie 貼到 config.txt
   3. 重新執行即可（不用改主程式）
"""

import requests
import sqlite3
import os
import time
import random
from datetime import datetime

# ============================================================
# 設定區 — 通常不需要修改
# ============================================================

# 此腳本所在目錄
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# 設定檔路徑（與此腳本同目錄下的 config.txt）
CONFIG_PATH = os.path.join(BASE_DIR, "config.txt")

# 人民幣換新台幣匯率（1 RMB = ? TWD）
RMB_TO_TWD = 4.5

# SQLite 資料庫路徑
DB_PATH = os.path.join(BASE_DIR, "cs2_prices.db")

# 請求間隨機休眠區間（秒），避免被偵測為爬蟲
SLEEP_MIN = 4
SLEEP_MAX = 7

# ============================================================
# 熱門搬磚皮膚清單（可自行增減）
# ============================================================

HOT_SKINS = [
    ("AK-47 | Fire Serpent (Minimal Wear)", "AK-47 | 火蛇 (略有磨损)"),
    ("AK-47 | Fire Serpent (Field-Tested)", "AK-47 | 火蛇 (久经沙场)"),
    ("AWP | Asiimov (Field-Tested)", "AWP | 二西莫夫 (久经沙场)"),
    ("AWP | Asiimov (Battle-Scarred)", "AWP | 二西莫夫 (战痕累累)"),
    ("AWP | Dragon Lore (Field-Tested)", "AWP | 巨龙传说 (久经沙场)"),
    ("Desert Eagle | Blaze (Factory New)", "沙漠之鹰 | 烈焰 (崭新出厂)"),
    ("M4A4 | Howl (Field-Tested)", "M4A4 | 咆哮 (久经沙场)"),
    ("M4A1-S | Printstream (Factory New)", "M4A1 消音型 | 印花集 (崭新出厂)"),
    ("AK-47 | Redline (Field-Tested)", "AK-47 | 红线 (久经沙场)"),
    ("USP-S | Kill Confirmed (Minimal Wear)", "USP 消音版 | 击杀确认 (略有磨损)"),
    ("Glock-18 | Fade (Factory New)", "Glock-18 | 渐变之色 (崭新出厂)"),
    ("Butterfly Knife | Fade (Factory New)", "蝴蝶刀 | 渐变之色 (崭新出厂)"),
    ("Karambit | Doppler (Factory New)", "爪子刀 | 多普勒 (崭新出厂)"),
    ("M9 Bayonet | Doppler (Factory New)", "M9 刺刀 | 多普勒 (崭新出厂)"),
    ("Talon Knife | Fade (Factory New)", "折叠刀 | 渐变之色 (崭新出厂)"),
]

# 建立英文→中文對照字典，方便查詢
SKIN_CN = {eng: cn for eng, cn in HOT_SKINS}
# 保留純英文清單供 API 查詢使用
SKIN_NAMES = [eng for eng, _ in HOT_SKINS]

# ============================================================
# 設定檔讀取
# ============================================================


def load_cookie() -> str:
    """從 config.txt 讀取 BUFF Cookie。

    優先順序：
      1. 環境變數 BUFF_COOKIE（可覆蓋設定檔）
      2. config.txt 中的 BUFF_COOKIE= 設定
      3. 空字串（未設定）

    Returns:
        Cookie 字串，若未設定則回傳空字串。
    """
    # 優先檢查環境變數
    env_cookie = os.environ.get("BUFF_COOKIE")
    if env_cookie:
        print("[信息] 使用環境變數 BUFF_COOKIE")
        return env_cookie

    # 從 config.txt 讀取
    if not os.path.exists(CONFIG_PATH):
        print(f"[錯誤] 找不到設定檔：{CONFIG_PATH}")
        return ""

    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("BUFF_COOKIE="):
                    cookie = line[len("BUFF_COOKIE="):].strip()
                    if cookie:
                        print("[信息] 從 config.txt 讀取 Cookie 成功")
                        return cookie

        print(f"[錯誤] config.txt 中未找到 BUFF_COOKIE= 設定")
        return ""

    except IOError as e:
        print(f"[錯誤] 讀取 config.txt 失敗：{e}")
        return ""


def save_cookie_to_config(cookie_value: str) -> tuple[bool, str]:
    """將 Cookie 寫入 config.txt。

    Args:
        cookie_value: 要儲存的 Cookie 字串。

    Returns:
        (成功與否, 訊息字串)
    """
    try:
        lines = []
        found = False

        # 讀取現有 config.txt
        if os.path.exists(CONFIG_PATH):
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip().startswith("BUFF_COOKIE="):
                        lines.append(f"BUFF_COOKIE={cookie_value}\n")
                        found = True
                    else:
                        lines.append(line)

        # 若不存在或沒有 BUFF_COOKIE= 行，則追加
        if not found:
            if not lines:
                lines.append("# BUFF 163 Cookie - 從瀏覽器複製貼上這裡\n")
            lines.append(f"\nBUFF_COOKIE={cookie_value}\n")

        # 寫回檔案
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            f.writelines(lines)

        return True, f"Cookie 已儲存（{len(cookie_value)} 字元）"

    except IOError as e:
        return False, f"寫入 config.txt 失敗：{e}"


def run_sync(cookie: str, progress_callback=None) -> tuple[int, int, list]:
    """執行一次完整的價格同步流程。

    Args:
        cookie: BUFF Cookie 字串。
        progress_callback: 可選的進度回呼函數，簽名為 (current, total, skin_name, status)。

    Returns:
        (成功筆數, 總筆數, 結果列表)
    """
    results = []
    total = len(SKIN_NAMES)

    for i, skin_name in enumerate(SKIN_NAMES, 1):
        if progress_callback:
            progress_callback(i, total, skin_name, "查詢中...")

        goods = search_buff_goods(skin_name, cookie)
        if goods is None:
            if progress_callback:
                progress_callback(i, total, skin_name, "❌ 查詢失敗")
            continue

        info = extract_price_info(goods, skin_name)
        if info:
            results.append(info)
            if progress_callback:
                progress_callback(i, total, skin_name, f"✅ NT${info[1]:.2f}")
        else:
            if progress_callback:
                progress_callback(i, total, skin_name, "❌ 解析失敗")

        if i < total:
            sleep_time = round(random.uniform(SLEEP_MIN, SLEEP_MAX), 1)
            time.sleep(sleep_time)

    # 寫入資料庫
    conn = init_database(DB_PATH)
    inserted = save_to_database(conn, results)
    conn.close()

    return inserted, total, results


# ============================================================
# 函數定義
# ============================================================


def init_database(db_path: str) -> sqlite3.Connection:
    """初始化 SQLite 資料庫，建立 buff_prices 資料表（若不存在）。"""
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS buff_prices (
            market_hash_name TEXT PRIMARY KEY,
            price_twd       REAL,
            volume          INTEGER,
            last_updated    TEXT
        )
    """)
    conn.commit()
    return conn


def search_buff_goods(skin_name: str, cookie: str, max_retries: int = 2) -> dict | None:
    """查詢 BUFF 163 單一皮膚的商品資訊。

    Args:
        skin_name: 皮膚的 market_hash_name（英文全稱）。
        cookie: BUFF 登入後的 Cookie 字串。
        max_retries: 429 時最多重試次數（預設 2 次）。

    Returns:
        BUFF 回傳的 JSON 中第一個完全匹配的商品資料 dict，或 None。
    """
    url = "https://buff.163.com/api/market/goods"
    params = {"game": "csgo", "search": skin_name}
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Cookie": cookie,
        "Referer": "https://buff.163.com/",
        "Accept": "application/json, text/plain, */*",
    }

    for attempt in range(1, max_retries + 2):
        try:
            resp = requests.get(url, params=params, headers=headers, timeout=15)

            # 429 Too Many Requests — 重試
            if resp.status_code == 429:
                wait = 5 * attempt  # 第1次等5秒，第2次等10秒...
                print(f"  ⏳ Rate limited (429)，等待 {wait} 秒後重試 (第{attempt}次)...")
                time.sleep(wait)
                continue

            resp.raise_for_status()
            data = resp.json()

            if data.get("code") != "OK":
                print(f"  ⚠️  API 回傳異常：{data.get('code')} - {data.get('error', '')}")
                return None

            goods_list = data.get("data", {}).get("items", [])
            if not goods_list:
                print(f"  ⚠️  找不到此皮膚：{skin_name}")
                return None

            # 優先找完全匹配的結果
            for goods in goods_list:
                name = goods.get("market_hash_name") or goods.get("name", "")
                if name.lower() == skin_name.lower():
                    return goods

            # 沒有完全匹配，回傳第一個
            return goods_list[0]

        except requests.exceptions.Timeout:
            print(f"  [錯誤] 請求超時：{skin_name}")
            return None
        except requests.exceptions.HTTPError as e:
            if resp.status_code in (401, 403):
                print(f"  [錯誤] Cookie 可能已過期或無效 (HTTP {resp.status_code})")
            else:
                print(f"  [錯誤] HTTP {resp.status_code}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"  [錯誤] 請求失敗：{e}")
            return None
        except ValueError:
            print(f"  [錯誤] JSON 解析失敗")
            return None

    print(f"  [錯誤] 已達最大重試次數，跳過此皮膚")
    return None


def extract_price_info(goods: dict, skin_name: str) -> tuple | None:
    """從 BUFF 商品資料中提取價格與交易量。

    Args:
        goods: BUFF API 回傳的單一商品資料 dict。
        skin_name: 皮膚名稱（用於錯誤訊息）。

    Returns:
        (market_hash_name, price_twd, volume, last_updated) 或 None。
    """
    try:
        sell_min_price = goods.get("sell_min_price")
        volume = goods.get("sell_num") or goods.get("volume") or 0
        market_hash = goods.get("market_hash_name") or goods.get("name") or skin_name

        if sell_min_price is None:
            print(f"  ⚠️  {skin_name} 無 sell_min_price 數據")
            return None

        try:
            price_rmb = float(sell_min_price)
        except (TypeError, ValueError):
            print(f"  ⚠️  {skin_name} sell_min_price 格式異常：{sell_min_price}")
            return None

        # 轉換為新台幣
        price_twd = round(price_rmb * RMB_TO_TWD, 2)

        try:
            volume = int(volume)
        except (TypeError, ValueError):
            volume = 0

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (market_hash, price_twd, volume, now_str)

    except Exception as e:
        print(f"  [錯誤] 解析 {skin_name} 時發生意外錯誤：{e}")
        return None


def save_to_database(conn: sqlite3.Connection, rows: list[tuple]) -> int:
    """將價格資料寫入 SQLite（INSERT OR REPLACE）。"""
    if not rows:
        print("  [注意] 無資料可寫入")
        return 0

    cursor = conn.cursor()
    inserted = 0

    for row in rows:
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO buff_prices
                (market_hash_name, price_twd, volume, last_updated)
                VALUES (?, ?, ?, ?)
            """, row)
            inserted += 1
        except sqlite3.Error as e:
            print(f"  [警告] 寫入失敗 ({row[0]}): {e}")
            continue

    conn.commit()
    return inserted


def print_summary(conn: sqlite3.Connection):
    """印出資料庫摘要統計。"""
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*), SUM(volume) FROM buff_prices")
    count, total_volume = cursor.fetchone()
    total_volume = total_volume or 0

    print("\n📊 當前資料庫內容：")
    print(f"   {'皮膚名稱':<55} {'價格 (TWD)':<12} {'成交量':<8} {'更新時間'}")
    print(f"   {'─'*55} {'─'*12} {'─'*8} {'─'*19}")
    cursor.execute(
        "SELECT market_hash_name, price_twd, volume, last_updated "
        "FROM buff_prices ORDER BY price_twd DESC"
    )
    for name, price, vol, ts in cursor.fetchall():
        print(f"   {name:<55} NT${price:<8.2f} {vol:<8,} {ts}")

    print(f"\n   📈 共 {count} 個皮膚，總成交量約 {total_volume:,} 件")


# ============================================================
# 主流程
# ============================================================

def main():
    print("=" * 60)
    print("  CS2 搬磚價格查詢工具 — BUFF 163")
    print("=" * 60)

    # 讀取 Cookie（config.txt → 環境變數覆蓋）
    print()
    buff_cookie = load_cookie()

    if not buff_cookie:
        print("\n❌ 錯誤：未設定 BUFF Cookie！")
        print("   請編輯 config.txt，將 Cookie 貼到 BUFF_COOKIE= 後面")
        print("   或設定環境變數：export BUFF_COOKIE='你的cookie'")
        print(f"\n   設定檔路徑：{CONFIG_PATH}")
        return

    # Step 1: 初始化資料庫
    print(f"\n[1/3] 初始化資料庫...")
    conn = init_database(DB_PATH)
    print(f"      資料庫路徑：{DB_PATH}")
    print(f"      待查詢皮膚：{len(SKIN_NAMES)} 個")

    # Step 2: 遍歷清單查詢 BUFF
    print(f"\n[2/3] 開始查詢 BUFF 價格（隨機休眠 {SLEEP_MIN}-{SLEEP_MAX} 秒/次）...")
    results = []
    total = len(SKIN_NAMES)

    for i, skin_name in enumerate(SKIN_NAMES, 1):
        print(f"\n  [{i}/{total}] {skin_name}")

        goods = search_buff_goods(skin_name, buff_cookie)
        if goods is None:
            print(f"  → ❌ 查詢失敗")
        else:
            info = extract_price_info(goods, skin_name)
            if info:
                _, price_twd, volume, _ = info
                print(f"  → ✅ NT${price_twd:.2f} (RMB${price_twd/RMB_TO_TWD:.2f}) | 成交量 {volume:,}")
                results.append(info)
            else:
                print(f"  → ❌ 解析失敗")

        if i < total:
            sleep_time = round(random.uniform(SLEEP_MIN, SLEEP_MAX), 1)
            print(f"    休眠 {sleep_time} 秒...")
            time.sleep(sleep_time)

    # Step 3: 寫入資料庫
    print(f"\n[3/3] 寫入資料庫...")
    inserted = save_to_database(conn, results)
    print(f"      成功寫入 {inserted}/{len(SKIN_NAMES)} 筆資料")

    print_summary(conn)
    conn.close()

    print(f"\n✅ 執行完畢！({datetime.now().strftime('%H:%M:%S')})")


if __name__ == "__main__":
    main()
