"""cdp_fb_crawler.py — Vision 定位 + PIL 裁切精讀（混合方案）"""
import time, sys, os, io
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

def _vision(img_bytes, prompt):
    try:
        import vision_analyzer as va
        return va.analyze_image(img_bytes, custom_prompt=prompt, retry=0)
    except:
        return None

def fetch_posts(max_scrolls=50, max_posts=15):
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")

        page = None
        for p in browser.contexts[0].pages:
            if 'facebook.com/groups/allinunderdog' in p.url:
                page = p; break
        if not page:
            page = browser.contexts[0].new_page()
            page.goto("https://www.facebook.com/groups/allinunderdog", timeout=25000)
            time.sleep(5)

        page.bring_to_front(); time.sleep(1)
        page.set_viewport_size({"width": 1280, "height": 2000})
        page.evaluate("document.body.style.zoom='0.8'")
        time.sleep(1)
        page.evaluate("window.scrollTo(0,0)")
        time.sleep(1)
        page.reload()
        time.sleep(5)

        page.evaluate("""[...document.querySelectorAll('[role="tab"]')].find(el=>el.innerText.includes('商品買賣'))?.click();""")
        time.sleep(2)

        page.evaluate("""
        const b=[...document.querySelectorAll('[role="tab"],[role="button"]')].find(el=>el.innerText.includes('新貼文'));
        if(b){b.click();setTimeout(()=>{
            [...document.querySelectorAll('[role="menuitem"],[role="option"]')].find(el=>el.innerText.includes('新貼文'))?.click();
        },300);}
        """)
        time.sleep(3)

        all_posts = []; seen = set()

        for scroll_i in range(max_scrolls):
            page.evaluate("window.scrollBy(0, 400)")
            time.sleep(0.8)

            img_bytes = page.screenshot(type="jpeg", quality=80)
            full_img = Image.open(io.BytesIO(img_bytes))
            img_w, img_h = full_img.size

            # Step 1: Vision 找出所有貼文位置
            buf = io.BytesIO()
            full_img.save(buf, format="JPEG", quality=70)

            items = _vision(buf.getvalue(),
                ("CS2交易助手。看這張FB商品買賣截圖。\n"
                 "找出所有**賣家**貼文，回傳JSON陣列：\n"
                 "{author:作者, skin:皮膚英文含磨損, price:數字, currency:TWD或RMB, y_top:貼文頂部px, y_bottom:底部px}\n"
                 "y_top/y_bottom是這張圖內的像素位置。排除收購/換物/無價貼文。沒有則[]。"))

            if not items or not isinstance(items, list):
                continue

            for it in items:
                a = str(it.get('author', ''))[:8]
                s = str(it.get('skin', ''))
                p = str(it.get('price', '0'))
                cur = str(it.get('currency', 'TWD')).upper()
                yt = it.get('y_top', 0)
                yb = it.get('y_bottom', 0)

                if yt <= 0 or yb <= 0 or yb - yt < 60:
                    continue

                try:
                    pv = int(float(p.replace(',', '')))
                except:
                    continue
                if cur == 'RMB':
                    pv = int(pv * 4.5)
                if pv <= 0 or len(s) < 2:
                    continue

                key = f"{a[:4]}|{s[:15]}|{pv}"
                if key in seen:
                    continue

                # Step 2: 裁切貼文區域，發給 Vision 精讀
                if yb - yt < 60:
                    continue

                crop = full_img.crop((0, max(0, yt - 5), img_w, min(img_h, yb + 5)))
                crop_buf = io.BytesIO()
                crop.save(crop_buf, format="JPEG", quality=85)

                detail = _vision(crop_buf.getvalue(),
                    ("CS2交易貼文細節確認。只回傳JSON。\n"
                     "{author:作者, skin:英文含磨損, price:數字, currency:TWD或RMB}\n"
                     "注意：打勾(✔)的才是要賣的皮膚。非賣家回傳null。"))

                if detail and isinstance(detail, dict):
                    a2 = str(detail.get('author', a))[:8]
                    s2 = str(detail.get('skin', s))
                    p2 = str(detail.get('price', str(pv)))
                    cur2 = str(detail.get('currency', cur)).upper()
                    try:
                        pv2 = int(float(p2.replace(',', '')))
                        if cur2 == 'RMB':
                            pv2 = int(pv2 * 4.5)
                    except:
                        pv2 = pv
                    if pv2 > 0 and len(s2) >= 2:
                        s, pv = s2, pv2
                        if a2:
                            a = a2

                seen.add(key)
                all_posts.append({
                    "id": f"p{len(all_posts)}", "author": a,
                    "content": f"【售】{s}\n賣 {pv} 台幣",
                    "link": "https://www.facebook.com/groups/allinunderdog"
                })
                print(f"  [FB] #{len(all_posts)} {a} — {s} NT${pv:,}")

            if len(all_posts) >= max_posts:
                break

        browser.close()
        print(f"  [FB] ✅ {len(all_posts)} 篇（{scroll_i+1} 次）")
        return all_posts
