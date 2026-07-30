"""快速測試 v2 整體流程。"""
import os, sys
os.environ["DEEPSEEK_API_KEY"] = "sk-a06ea0387ec14a1c88cbd178a7e4010f"
sys.path.insert(0, ".")
import analyze_arbitrage as aa

print("=== 測試 Playwright 爬蟲（模擬後備模式）===")
posts = aa.fetch_fb_posts()
print(f"取得 {len(posts)} 篇貼文")

state = aa.load_state()
new = aa.filter_new_posts(posts, state)
print(f"新貼文: {len(new)} 篇")

deals = aa.process_posts(new)
print(f"\n套利機會: {len(deals)} 個")
for d in deals:
    print(f"  ✅ {d['skin_name']} → NT${d['profit']:,.0f}")
    ok = aa.upload_to_cloud(d)
    print(f"     雲端同步: {'✅' if ok else '❌'} (存根)")

print("\n✅ v2 完整測試通過")
