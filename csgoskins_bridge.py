"""csgoskins_bridge.py — 精準磨損度 BUFF163 查價（每步都有延遲）"""
import re, time, json, requests
from playwright.sync_api import sync_playwright

WEARS = {
    "Factory New": "FN", "Minimal Wear": "MW",
    "Field-Tested": "FT", "Well-Worn": "WW", "Battle-Scarred": "BS",
}

def to_slug(name: str) -> str:
    n = name.replace('★ ', '').replace('™', '').replace(' | ', '-')
    n = n.replace('(', '').replace(')', '').replace("'", '').replace('  ', ' ')
    for w in WEARS:
        n = n.replace(f' {w}', '').replace(f'-{w.lower()}', '')
    return n.lower().strip().replace(' ', '-').replace('--', '-')

def fetch_buff_price(skin_name: str) -> dict | None:
    slug = to_slug(skin_name)

    # 找出目標磨損度
    target_wear = None
    for w in WEARS:
        if w in skin_name:
            target_wear = w
            break
    if not target_wear:
        return None
    wear_short = WEARS[target_wear]

    try:
        requests.get("http://127.0.0.1:9222/json/version", timeout=3)
    except Exception:
        return None

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
            page = browser.contexts[0].new_page()
            page.goto(f"https://csgoskins.gg/items/{slug}", wait_until="domcontentloaded", timeout=25000)
            time.sleep(2)  # ← 等頁面完全載入
            page.evaluate("window.scrollBy(0, 200)")
            time.sleep(1)

            # === Step 2: 點選目標磨損度 ===
            # 選擇器：可能是 <A> 連結或 <DIV> 方塊
            for selector in [f"a:has-text('{wear_short}'):has-text('{target_wear}')",
                             f"div:has-text('{wear_short}')"]:
                btn = page.locator(selector).first
                if btn.count() > 0:
                    btn.click()
                    time.sleep(2)
                    break

            # === Step 4: 切 SELL ===
            sell = page.locator("button, [role='tab']").filter(has_text="SELL")
            if sell.count() > 0:
                sell.first.click()
                time.sleep(2)  # ← 等 SELL 資料載入
                page.evaluate("window.scrollBy(0, 200)")
                time.sleep(1)

            # === Step 5: 讀取 BUFF163 價格 ===
            text = page.inner_text("body")
            page.close()

            lines = text.split('\n')
            for i, line in enumerate(lines):
                if line.strip() == 'BUFF163':
                    for j in range(i, min(i + 8, len(lines))):
                        m = re.search(r'¥([0-9,]+\.?\d*)', lines[j])
                        if m:
                            rmb = float(m.group(1).replace(',', ''))
                            twd = rmb * 4.5
                            return {"price_twd": round(twd, 2), "price_rmb": rmb, "wear": target_wear}

            return None

        except Exception:
            return None
