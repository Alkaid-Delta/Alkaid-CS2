"""
cdp_fb_crawler.py — GraphQL API 攔截方案
取代舊的 Vision 截圖方式，直接從 FB 的 API 拿到精確文字資料
"""
import time, json, os, sys, subprocess
from datetime import datetime

def _get_cfg(key: str, default: str = "") -> str:
    """從 config.txt 讀取設定"""
    cfg_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.txt")
    if os.path.exists(cfg_path):
        with open(cfg_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line.startswith(f"{key}="):
                    return line[len(key)+1:].strip()
    return os.environ.get(key, default)

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

        # 滾動載入更多 — 模擬人類隨機滾動
        all_posts = []
        seen_ids = set()
        import random as _rand

        for i in range(max_scrolls):
            scroll_px = _rand.randint(500, 1200)
            page.evaluate(f"window.scrollBy(0, {scroll_px})")
            time.sleep(_rand.uniform(1.5, 3.5))

            # 解析本輪新收集到的 body
            new_posts = []
            for body in all_bodies:
                posts = _extract_posts_from_body(body)
                for p in posts:
                    pid = p['id']
                    if pid and pid not in seen_ids:
                        seen_ids.add(pid)
                        new_posts.append(p)

            all_bodies.clear()

            # 從 DOM 的 feed 容器抓取貼文+圖片（精準綁定，不錯配）
            if new_posts:
                try:
                    dom_posts = page.evaluate('''() => {
                        const results = [];
                        // 貼文容器：feed 的直接子層
                        const feed = document.querySelector('div[role="feed"]');
                        const containers = feed ? feed.children : [];
                        
                        for (const container of containers) {
                            // 文字節點
                            let text = '';
                            const msgNode = container.querySelector('div[data-ad-comet-preview="message"]');
                            if (msgNode) {
                                text = (msgNode.textContent || '').trim();
                            }
                            if (!text) {
                                const dirNode = container.querySelector('div[dir="auto"]');
                                if (dirNode) text = (dirNode.textContent || '').trim();
                            }
                            
                            // 圖片節點（優先 srcset 高清，其次 src）
                            const imgs = [];
                            container.querySelectorAll('img[src*="fbcdn"], img[src*="scontent"], a[href*="photo"] img').forEach(img => {
                                let src = '';
                                if (img.srcset) {
                                    src = img.srcset.split(' ')[0];  // 最高解析度
                                } else {
                                    src = img.src || '';
                                }
                                if (src && !src.startsWith('data:') && !src.includes('static') 
                                    && !imgs.includes(src) && img.width > 50) {
                                    imgs.push(src);
                                }
                            });
                            
                            if (text || imgs.length > 0) {
                                results.push({text: text.substring(0, 200), images: imgs});
                            }
                        }
                        return results;
                    }''')

                    # 用文字比對方式配對 DOM 貼文到 API 貼文
                    for p in new_posts:
                        api_text = (p.get('text') or '')[:40].strip()
                        if not api_text:
                            continue
                        for dp in dom_posts:
                            dom_text = (dp.get('text') or '')[:40].strip()
                            # 文字有重疊就配對
                            if dom_text and api_text and (
                                dom_text[:15] in api_text or api_text[:15] in dom_text
                            ):
                                if dp.get('images'):
                                    p['images'] = dp['images'][:3]
                                if dp.get('text') and len(dp['text']) > len(p.get('text','')):
                                    p['alt_text'] = dp['text']
                                break
                except Exception:
                    pass

            all_posts.extend(new_posts)
            print('.', end='', flush=True)

            if len(all_posts) >= max_posts:
                print()
                break

        print(f" {len(all_posts)} 篇")

        # ── 圖片優先：每張圖獨立分析，各自綁定名稱/磨損/價格（互不污染）──
        for p in all_posts:
            if not p['images']:
                continue
            # 每張圖的結果獨立存到 items，不塞進文字
            p['items'] = []

            # 如果 FB 已 OCR 圖片 alt text，直接用它（不用 Vision，省成本）
            alt_text = p.get('alt_text', '')
            if alt_text:
                p['text'] = f"[圖片] {alt_text[:200]}"
                print(f"  [FB] 🖼️ 圖片 alt: {alt_text[:80]}")
                continue

            # 否則用 Vision 讀圖（先判斷類型：庫存列表→切格子 / 單一物品→整圖）
            try:
                import requests as _req
                import vision_analyzer as va
                if not os.environ.get("OPENROUTER_API_KEY"):
                    os.environ["OPENROUTER_API_KEY"] = _get_cfg("OPENROUTER_API_KEY", "")

                for img_url in p['images'][:3]:  # 每張圖獨立處理
                    resp = _req.get(img_url, timeout=15)
                    if resp.status_code != 200:
                        continue

                    # ── Step 1: 判斷圖片類型 ──
                    type_result = va.analyze_image(
                        resp.content,
                        custom_prompt=(
                            "判斷這張 CS2 截圖類型，只回傳 JSON:"
                            '{"type":"inventory"或"single"或"other",'
                            '"rows":數字,"cols":數字}'
                            "inventory=多物品庫存列表(有格子), rows/cols=格子數(如3列7行)"
                            "single=單一物品詳情頁/Steam頁/遊戲內檢視"
                        ),
                        retry=1
                    )
                    if type_result and isinstance(type_result, dict):
                        img_type = type_result.get('type', 'other')
                        rows = int(type_result.get('rows') or 7)
                        cols = int(type_result.get('cols') or 3)
                    else:
                        img_type = 'single'
                        rows, cols = 7, 3

                    # ── Step 2: 依類型分流 ──
                    if img_type == 'inventory':
                        # 庫存列表 → 無法可靠辨識(手繪勾/多物品)，直接跳過（規則:不勉強）
                        print(f"  [FB] 🖼️ 庫存列表圖,跳過(無法可靠辨識)")
                        continue
                    else:
                        # 單一物品頁 → 整圖讀取
                        result = va.analyze_image(
                            resp.content,
                            custom_prompt=(
                                "CS2交易截圖.提取要賣的物品,輸出JSON陣列:"
                                '[{"name":"完整中文名含★","wear":"磨損度","price":數字,"currency":"TWD/RMB"}]'
                                "單一物品頁就是那件.無法辨識回傳[]"
                            ),
                            retry=1
                        )
                        if result and isinstance(result, list) and len(result) > 0:
                            items = result
                        elif result and isinstance(result, dict):
                            items = [result]
                        else:
                            items = []

                        for item in items:
                            cn = item.get('chinese_name', item.get('name', ''))
                            wear = item.get('wear', '')
                            price = item.get('price', 0)
                            cur = item.get('currency', 'RMB')
                            st = "StatTrak " if item.get('stattrak') else ""
                            if cn:
                                p['items'].append({
                                    "name": f"{st}{cn}",
                                    "wear": wear,
                                    "price": price,
                                    "currency": cur,
                                })

                    if p['items']:
                        n = len(p['items'])
                        print(f"  [FB] 🖼️ 圖片: {n} 件物品 (第一件: {p['items'][0]['name'][:40]})")
                        break  # 讀到一張圖的結果就夠了
            except ImportError:
                pass
            except Exception as e:
                print(f"  [FB] ⚠️ Vision 錯誤: {e}")

        browser.close()

    # ── 轉換成標準輸出格式 ──
    # 每張圖的每個物品 = 獨立一筆候選（解決多物品貼文）
    results = []
    for i, p in enumerate(all_posts[:max_posts]):
        items = p.get('items') or []
        if items:
            # 多物品：每件獨立成一筆，共用作者/連結
            for j, item in enumerate(items):
                skin_text = f"[圖片] 售 {item['name']} {item['wear']}"
                if item.get('price'):
                    skin_text += f" {item['price']}{item['currency']}"
                results.append({
                    "id": f"p{i}_item{j}",
                    "author": p['author'],
                    "content": skin_text,
                    "link": p['url'] or "https://www.facebook.com/groups/allinunderdog",
                })
        else:
            results.append({
                "id": f"p{i}",
                "author": p['author'],
                "content": p['text'],
                "link": p['url'] or "https://www.facebook.com/groups/allinunderdog"
            })

    print(f"  [FB] ✅ {len(results)} 筆（含多物品拆分）")
    return results
