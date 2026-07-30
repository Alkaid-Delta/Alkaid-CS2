"""完整測試 v2：修正 DeepSeek prompt"""
import subprocess, sys, json, os, time

BASE = os.path.dirname(os.path.abspath(__file__))
api_key = os.environ.get("DEEPSEEK_API_KEY")
if not api_key:
    print("❌ 請設定 DEEPSEEK_API_KEY 環境變數")
    exit(1)
os.environ["DEEPSEEK_API_KEY"] = api_key

print("=== 1. 抓 FB 貼文 ===")
from cdp_fb_crawler import fetch_posts
posts = fetch_posts(max_scrolls=10, max_posts=3)
for p in posts:
    print(f"  [{p['author']}] {p['content'][:100]}")

print("\n=== 2. 分析套利 ===")
import requests

deals = []
for post in posts:
    text = post["content"]
    author = post["author"]
    print(f"\n[{author}] 分析中...")
    
    prompt = f"""你是CS2皮膚識別專家.分析以下FB貼文.

關鍵字: 「售」「賣」「出」開頭=賣家,「收」「換」「求」開頭=非賣家

貼文: {text}

如果是賣家，回傳 JSON: {{"market_hash_name":"完整英文名含磨損","seller_price":數字(無=0),"confidence":"high/medium/low"}}
非賣家，回傳 JSON: {{"market_hash_name":"NONE"}}"""
    
    try:
        resp = requests.post(
            "https://api.deepseek.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.1, "max_tokens": 200,
                "response_format": {"type": "json_object"}
            }, timeout=30
        )
        info = json.loads(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        print(f"  ❌ API錯誤: {e}")
        continue
    
    if info.get("market_hash_name") == "NONE":
        print(f"  ➖ 非賣家")
        continue
    
    skin = info["market_hash_name"]
    price = info.get("seller_price", 0)
    conf = info.get("confidence", "low")
    print(f"  ✅ {skin}")
    print(f"     價格: NT${price:,} | 信心: {conf}")
    
    if price <= 0:
        print(f"     ⚠️ 無價格，跳過")
        continue
    
    # 查 BUFF 價 (用 subprocess 跑 csgoskins_bridge)
    print(f"     查價...", end=" ", flush=True)
    result = subprocess.run(
        [sys.executable, "-c",
         f"import sys; sys.path.insert(0, {json.dumps(BASE)}); "
         f"import csgoskins_bridge; "
         f"r = csgoskins_bridge.fetch_buff_price({json.dumps(skin)}); "
         f"import json; print(json.dumps(r) if r else 'null')"],
        capture_output=True, text=True, timeout=30
    )
    out = result.stdout.strip()
    if out and out != "null":
        buff = json.loads(out)
        rmb = buff["price_rmb"]
        twd = buff["price_twd"]
        print(f"BUFF ¥{rmb:,.2f} = NT${twd:,.0f}")
        
        net = twd * 0.985
        profit = net - price
        margin = (profit / price * 100) if price > 0 else 0
        sign = "+" if profit >= 0 else ""
        print(f"     利潤: {sign}NT${profit:,.0f} ({margin:.1f}%)")
        
        deals.append({
            "skin": skin,
            "seller": author,
            "seller_price": price,
            "buff_price": twd,
            "profit": profit,
            "margin": margin
        })
    else:
        print("查無價格")

print(f"\n=== 結果: {len(deals)} 筆 ===")
for d in deals:
    sign = "+" if d['profit'] >= 0 else ""
    print(f"  [{d['seller']}] {d['skin']}")
    print(f"    賣NT${d['seller_price']:>,} vs BUFF NT${d['buff_price']:>,}")
    print(f"    利潤: {sign}NT${d['profit']:,.0f} ({d['margin']:.1f}%)")
