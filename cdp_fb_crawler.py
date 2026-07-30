"""
cdp_fb_crawler.py — GraphQL API 攔截方案
取代舊的 Vision 截圖方式，直接從 FB 的 API 拿到精確文字資料
"""
import time, json, os, sys, subprocess
from datetime import datetime

def _ensure_chrome_debug():
    """確保 Chrome 執行在除錯模式（port 9222），若無則自動啟動"""
    import urllib.request
    try:
        r = urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=3)
        return True
    except Exception:
        pass

    # 啟動 Chrome 除錯模式
    chrome = "C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"
    user_data = "C:/Users/user/AppData/Local/Google/Chrome/User Data"
    try:
        subprocess.Popen([
            chrome,
            "--remote-debugging-port=9222",
            "--remote-allow-origins=*",
            f"--user-data-dir={user_data}",
            "--new-window",
            "https://www.facebook.com/groups/allinunderdog"
        ], shell=False)
        for _ in range(15):
            time.sleep(1)
            try:
                urllib.request.urlopen("http://127.0.0.1:9222/json/version", timeout=2)
                return True
            except Exception:
                continue
    except Exception as e:
        print(f"  [Chrome] 啟動失敗: {e}")
    return False


def _parse_batch_json(body):
    """解析 FB 批量 GraphQL 回應（多個 JSON object 串聯）"""
    results = []
    decoder = json.JSONDecoder()
    pos = 0
    while pos < len(body):
        try:
            obj, end = decoder.raw_decode(body, pos)
            results.append(obj)
            pos = end
            while pos < len(body) and body[pos] in ' \n\r\t':
                pos += 1
        except json.JSONDecodeError:
            break
    return results


def _extract_posts_from_body(body):
    """從一筆 group_feed API 回應中提取所有貼文"""
    posts = []
    try:
        objs = _parse_batch_json(body)
    except Exception:
        return posts

    for obj in objs:
        try:
            node = obj.get('data', {}).get('node', {})
        except AttributeError:
            continue
        if not node:
            continue

        feed = node.get('group_feed', {})
        edges = feed.get('edges', [])

        for edge in edges:
            try:
                sn = edge.get('node', {})
            except AttributeError:
                continue
            if not isinstance(sn, dict) or sn.get('__typename') != 'Story':
                continue

            # ── 作者 ──
            author = ''
            actors = sn.get('actors', [])
            if isinstance(actors, list) and actors:
                author = actors[0].get('name', '')

            # ── 內文 ──
            text = ''
            try:
                text = (sn['comet_sections']['content']
                        ['story']['comet_sections']['message']['text'])
            except (KeyError, TypeError):
                try:
                    text = (sn['comet_sections']['content']
                            ['story']['message']['text'])
                except (KeyError, TypeError):
                    pass

            # ── 時間 ──
            ts = sn.get('creation_time', 0)

            # ── 圖片 ──
            img_urls = []
            try:
                attachments = sn['comet_sections']['content']['story'].get('attachments', [])
                if isinstance(attachments, list):
                    for att in attachments:
                        if isinstance(att, dict):
                            media = att.get('media', {}) or {}
                            if isinstance(media, dict):
                                img = media.get('image', {}) or {}
                                if isinstance(img, dict) and img.get('uri'):
                                    img_urls.append(img['uri'])
            except (KeyError, TypeError):
                pass

            # ── 貼文 URL ──
            post_url = sn.get('permalink_url', '')

            post_id = sn.get('post_id', '') or sn.get('id', '')

            posts.append({
                "author": author,
                "text": text.strip(),
                "timestamp": ts,
                "images": img_urls,
                "url": post_url,
                "id": post_id
            })

    return posts


def fetch_posts(max_scrolls=50, max_posts=15):
    """
    透過 CDP 攔截 FB GraphQL API 取得社團貼文資料。

    Returns:
        list[dict]: [{id, author, content, link}, ...]
            - content 為純文字，不含 HTML
            - link 為貼文網址
    """
    # 確保 Chrome 在除錯模式
    if not _ensure_chrome_debug():
        print("  [FB] ❌ 無法啟動 Chrome 除錯模式，請手動執行啟動批次檔")
        return []

    from playwright.sync_api import sync_playwright

    all_bodies = []

    with sync_playwright() as pw:
        browser = pw.chromium.connect_over_cdp("http://127.0.0.1:9222")
        ctx = browser.contexts[0]

        # 找已存在的 FB 分頁
        page = None
        for p in ctx.pages:
            url = p.url
            if 'facebook.com/groups/allinunderdog' in url:
                page = p
                break

        if not page:
            page = ctx.new_page()
            page.goto("https://www.facebook.com/groups/allinunderdog",
                       timeout=30000, wait_until='domcontentloaded')
            time.sleep(5)

        # 確保在社團主頁
        if '/groups/allinunderdog' not in page.url:
            page.goto("https://www.facebook.com/groups/allinunderdog",
                       timeout=30000)
            time.sleep(5)

        # 設定網路攔截
        def on_response(response):
            if '/api/graphql' in response.url:
                try:
                    body = response.text()
                    # 只保留包含貼文資料的回應
                    if 'group_feed' in body and 'edges' in body:
                        all_bodies.append(body)
                except Exception:
                    pass

        page.on("response", on_response)

        # 重新載入以觸發 API 呼叫
        page.reload()
        time.sleep(6)

        print(f"  [FB] 滾動載入貼文...", end='', flush=True)

        # 滾動載入更多
        all_posts = []
        seen_ids = set()

        for i in range(max_scrolls):
            page.evaluate("window.scrollBy(0, 1200)")
            time.sleep(2.5)  # 給 FB API 足夠時間回應

            # 解析目前已收集到的 body
            for body in all_bodies:
                posts = _extract_posts_from_body(body)
                for p in posts:
                    pid = p['id']
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        all_posts.append(p)

            # 清掉已處理的 body
            all_bodies.clear()
            print('.', end='', flush=True)

            if len(all_posts) >= max_posts:
                print()
                break

        print(f" {len(all_posts)} 篇")

        # ── 對純圖片貼文補 Vision ──
        # 只讀圖片中的中文文字，不翻譯，讓 analyze_arbitrage 處理翻譯
        for p in all_posts:
            if p['images'] and len(p['text']) < 15:
                img_url = p['images'][0]
                try:
                    import requests
                    resp = requests.get(img_url, timeout=15)
                    if resp.status_code == 200:
                        try:
                            import vision_analyzer as va
                            result = va.analyze_image(
                                resp.content,
                                custom_prompt=(
                                    "CS2交易截圖。這張圖是 BUFF/Steam 的皮膚截圖。\n"
                                    "請讀取圖中的**中文**皮膚名稱和價格。\n"
                                    "回傳 JSON:\n"
                                    "{'chinese_name':'中文皮膚名不含磨損',\n"
                                    " 'wear':'磨損度中文(崭新/略有磨损/久经/破损不堪/战痕累累)',\n"
                                    " 'price':價格數字(無=0),\n"
                                    " 'currency':'TWD'或'RMB'}"
                                ),
                                retry=0
                            )
                            if result and isinstance(result, dict):
                                cn = result.get('chinese_name', '')
                                wear = result.get('wear', '')
                                price = result.get('price', 0)
                                cur = result.get('currency', 'TWD')
                                extra = f"【圖】{cn} {wear}"
                                if price:
                                    extra += f" {cur}{price}"
                                p['text'] = f"[圖片] 售 {cn} {wear} {price}{cur}"
                                print(f"  [FB] 🖼️ 圖片貼文: {cn} {wear} {price}{cur}")
                        except ImportError:
                            pass
                        except Exception as e:
                            print(f"  [FB] ⚠️ Vision 輔助失敗: {e}")
                except Exception:
                    pass

        browser.close()

    # ── 轉換成標準輸出格式 ──
    results = []
    for i, p in enumerate(all_posts[:max_posts]):
        results.append({
            "id": f"p{i}",
            "author": p['author'],
            "content": p['text'],
            "link": p['url'] or "https://www.facebook.com/groups/allinunderdog"
        })

    print(f"  [FB] ✅ {len(results)} 篇（API 攔截）")
    return results
