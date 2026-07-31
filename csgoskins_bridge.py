"""
csgoskins_bridge.py — BUFF163 查價（openskin.dev API 優先，csgoskins.gg 備用）
"""
import re, time, json, urllib.request, os

API_BASE = "https://api.openskin.dev/v1/prices/buff"
CACHE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "buff_cache.json")
CACHE_TTL = 300  # 5 分鐘

WEARS = {
    "Factory New": "FN", "Minimal Wear": "MW",
    "Field-Tested": "FT", "Well-Worn": "WW", "Battle-Scarred": "BS",
}

# === 快取 ===
_cache = None
def _load_cache():
    global _cache
    if _cache is None and os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, "r") as f:
                _cache = json.load(f)
        except:
            _cache = {}
    if _cache is None:
        _cache = {}

def _save_cache():
    with open(CACHE_FILE, "w") as f:
        json.dump(_cache, f)

def _get_from_cache(name):
    _load_cache()
    entry = _cache.get(name)
    if entry and time.time() - entry.get("t", 0) < CACHE_TTL:
        return entry.get("data")
    return None

def _set_cache(name, data):
    _load_cache()
    _cache[name] = {"data": data, "t": time.time()}
    _save_cache()


def _log(msg):
    """日誌輸出到 stderr，避免污染 stdout (subprocess 解析用)"""
    import sys
    print(msg, file=sys.stderr)


def fetch_buff_price(skin_name: str) -> dict | None:
    """查詢 BUFF 價格：openskin.dev API → csgoskins.gg 備用

    Returns:
        {"price_twd": float, "price_rmb": float, "wear": str} | None
    """
    # 1. 查快取
    cached = _get_from_cache(skin_name)
    if cached:
        return cached

    # 2. 找出磨損度
    target_wear = None
    for w in WEARS:
        if w in skin_name:
            target_wear = w
            break
    if not target_wear:
        return None

    # 3. openskin.dev API
    result = _try_openskin(skin_name, target_wear)
    if result:
        _set_cache(skin_name, result)
        return result

    # 4. 備用：csgoskins.gg
    result = _try_csgoskins(skin_name, target_wear)
    if result:
        _set_cache(skin_name, result)
    return result


def _try_openskin(skin_name: str, target_wear: str) -> dict | None:
    """透過 openskin.dev API 查價"""
    try:
        url = f"{API_BASE}?item={urllib.request.quote(skin_name)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json"
        })
        resp = urllib.request.urlopen(req, timeout=10)
        data = json.loads(resp.read().decode())

        ask = data.get("ask")
        if not ask:
            return None

        # openskin API 回傳 USD！需轉換: USD → RMB (×7.2) → TWD (×4.5)
        usd = float(ask)
        rmb = usd * 7.2
        twd = rmb * 4.5  # ≈ usd × 32.4
        result = {"price_twd": round(twd, 2), "price_rmb": round(rmb, 2), "wear": target_wear}
        _log(f"  [openskin] ✅ {skin_name} → ${usd:,.2f} USD = ¥{rmb:,.2f} RMB → NT${twd:,.0f}")
        return result
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _log(f"  [openskin] ⚠️ 查無此皮膚: {skin_name[:50]}")
        else:
            _log(f"  [openskin] ⚠️ HTTP {e.code}")
    except Exception as e:
        _log(f"  [openskin] ⚠️ {str(e)[:60]}")
    return None


def _try_csgoskins(skin_name: str, target_wear: str) -> dict | None:
    """備用：csgoskins.gg 爬蟲查價（維持現有邏輯）"""
    try:
        urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3)
    except Exception:
        return None

    from playwright.sync_api import sync_playwright

    slug = _to_slug(skin_name)
    wear_short = WEARS[target_wear]

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].new_page()
            page.goto(f"https://csgoskins.gg/items/{slug}",
                       wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)
            page.evaluate("window.scrollBy(0, 200)")
            time.sleep(1)

            for selector in [f"a:has-text('{wear_short}'):has-text('{target_wear}')",
                             f"div:has-text('{wear_short}')"]:
                btn = page.locator(selector).first
                if btn.count() > 0:
                    btn.click()
                    time.sleep(2)
                    break

            sell = page.locator("button, [role='tab']").filter(has_text="SELL")
            if sell.count() > 0:
                sell.first.click()
                time.sleep(2)
                page.evaluate("window.scrollBy(0, 200)")
                time.sleep(1)

            text = page.inner_text("body")
            page.close()

            for line in text.split('\n'):
                if line.strip() == 'BUFF163':
                    for j in range(i, min(i + 8, len(lines))):
                        m = re.search(r'¥([0-9,]+\\.?\\d*)', lines[j])
                        if m:
                            rmb = float(m.group(1).replace(',', ''))
                            twd = rmb * 4.5
                            return {"price_twd": round(twd, 2), "price_rmb": rmb, "wear": target_wear}
            return None
        except Exception:
            return None


def _to_slug(name: str) -> str:
    n = name.replace('★ ', '').replace('™', '').replace(' | ', '-')
    n = n.replace('(', '').replace(')', '').replace("'", '').replace('  ', ' ')
    for w in WEARS:
        n = n.replace(f' {w}', '').replace(f'-{w.lower()}', '')
    return n.lower().strip().replace(' ', '-').replace('--', '-')
