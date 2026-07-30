"""
app.py — CS2 搬磚價格查詢工具 (Streamlit 網頁介面)
===================================================
佈局：
  1. 系統狀態列（一行）
  2. 套利分析結果（最上方）
  3. Cookie 設定（簡約）
  4. BUFF 價格資料庫
  5. 手動同步價格
"""

import streamlit as st
import sqlite3
import os
import sys
import re
import pandas as pd
from datetime import datetime, time as dtime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import fetch_prices as fp
import analyze_arbitrage as aa

st.set_page_config(page_title="CS2 搬磚價格查詢", page_icon="🔫", layout="wide")

# ============================================================
# 輔助函數
# ============================================================

def is_operating_hours() -> bool:
    now = datetime.now().time()
    return dtime(8, 0) <= now <= dtime(23, 0)

def read_db_as_dataframe(db_path: str):
    if not os.path.exists(db_path):
        return pd.DataFrame()
    conn = sqlite3.connect(db_path)
    try:
        return pd.read_sql_query(
            "SELECT market_hash_name AS 皮膚名稱, price_twd AS 台幣價格, "
            "volume AS 成交量, last_updated AS 最後更新時間 "
            "FROM buff_prices ORDER BY price_twd DESC", conn)
    finally:
        conn.close()

KNOWN_WEARS = ["Factory New", "Minimal Wear", "Field-Tested", "Well-Worn", "Battle-Scarred"]
WEAR_SHORT = {"Factory New": "崭新出厂", "Minimal Wear": "略有磨损", "Field-Tested": "久经沙场",
              "Well-Worn": "战痕累累", "Battle-Scarred": "破损不堪"}
WEAR_ORDER = {w: i for i, w in enumerate(KNOWN_WEARS)}

def parse_skin_name(full_name: str) -> dict:
    wears_pattern = "|".join(KNOWN_WEARS)
    m = re.search(rf"\(({wears_pattern})\)$", full_name)
    if not m:
        return {"base": full_name, "wear": "", "prefix": "", "display_key": full_name}
    wear = m.group(1)
    bare = full_name[: m.start()].strip()
    prefix = ""
    if bare.startswith("★ StatTrak™ "): prefix = "🔟★"; bare = bare[13:]
    elif bare.startswith("StatTrak™ "):  prefix = "🔟";   bare = bare[10:]
    elif bare.startswith("Souvenir "):   prefix = "🎁";   bare = bare[9:]
    elif bare.startswith("★ "):          prefix = "★";    bare = bare[2:]
    return {"base": bare, "wear": wear, "prefix": prefix, "display_key": f"{prefix} {bare}".strip()}

def build_grouped_df(df):
    if df.empty:
        return None
    rows = []
    for _, row in df.iterrows():
        parsed = parse_skin_name(row["皮膚名稱"])
        rows.append({"display_key": parsed["display_key"], "base": parsed["base"],
                     "wear": parsed["wear"], "prefix": parsed["prefix"],
                     "price_twd": row["台幣價格"], "volume": row["成交量"],
                     "last_updated": row["最後更新時間"]})
    pdf = pd.DataFrame(rows)
    group_data = {}
    for _, r in pdf.iterrows():
        key = r["display_key"]
        if key not in group_data:
            group_data[key] = {"基底名稱": key, "中文名稱": fp.SKIN_CN.get(r["base"], "")}
        ws = WEAR_SHORT.get(r["wear"], r["wear"])
        group_data[key][f"price_{ws}"] = r["price_twd"]
        group_data[key][f"vol_{ws}"] = r["volume"]
    result = pd.DataFrame(group_data.values())
    cols = ["基底名稱", "中文名稱"]
    for w in KNOWN_WEARS:
        ws = WEAR_SHORT[w]
        pc, vc = f"price_{ws}", f"vol_{ws}"
        if pc in result.columns:
            cols.append(pc); cols.append(vc)
    for c in cols:
        if c not in result.columns:
            result[c] = None
    return result[cols]

# ============================================================
# Session State
# ============================================================

for key in ["sync_result", "save_msg", "show_cookie"]:
    if key not in st.session_state:
        st.session_state[key] = None if key != "show_cookie" else False
if "sync_running" not in st.session_state:
    st.session_state.sync_running = False

# ============================================================
# 標題 + 狀態列
# ============================================================

st.title("🔫 CS2 搬磚價格查詢工具")
st.caption("資料來源：BUFF 163 ｜ 匯率：1 RMB = 4.5 TWD")

try:
    ss = aa.get_scan_stats()
    src, fch, cnt = ss.get("source", "等待中"), ss.get("last_fetch", "從未"), ss.get("scanned_today", 0)
except Exception:
    src, fch, cnt = "等待中", "從未", 0

c1, c2, c3, c4 = st.columns(4)
c1.markdown(f"{'🟢 **運作中**' if is_operating_hours() else '🟡 **休眠中**'}　{datetime.now().strftime('%H:%M')}")
c2.markdown(f"📡 **來源**　{src}")
c3.markdown(f"🕐 **最近**　{fch}")
c4.markdown(f"📋 **今日**　{cnt} 篇")

# ============================================================
# 區塊 1：套利分析結果
# ============================================================

st.divider()
st.header("📋 近期套利分析結果")

deals = aa.get_recent_deals(limit=20)
if deals:
    rows = []
    for d in deals:
        rows.append({
            "時間": d.get("created_at", "")[5:16] if d.get("created_at") else "",
            "賣家": d.get("author", ""),
            "皮膚": d.get("skin_name", ""),
            "開價": f"NT${d['seller_price']:,.0f}",
            "BUFF價": f"NT${d['buff_price']:,.0f}",
            "利潤": f"+NT${d['profit']:,.0f} ({d['profit_margin']:.1f}%)",
            "成交量": f"{d['volume']:,}",
            "風險": {"low": "🟢 低", "medium": "🟡 中", "high": "🔴 高"}.get(d.get("risk", ""), ""),
            "AI分析": d.get("reason", ""),
        })
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True,
        column_config={
            "時間": st.column_config.TextColumn("時間", width="small"),
            "賣家": st.column_config.TextColumn("賣家", width="small"),
            "皮膚": st.column_config.TextColumn("皮膚", width="medium"),
            "開價": st.column_config.TextColumn("💰 賣家開價", width="small"),
            "BUFF價": st.column_config.TextColumn("🏷️ BUFF 最低價", width="small"),
            "利潤": st.column_config.TextColumn("📈 利潤", width="small"),
            "成交量": st.column_config.TextColumn("📦 量", width="small"),
            "風險": st.column_config.TextColumn("⚠️ 風險", width="small"),
            "AI分析": st.column_config.TextColumn("🧠 AI 分析", width="large"),
        })
else:
    st.info("📭 尚無分析結果，等掃描到 FB 貼文後會自動出現")

# ============================================================
# 區塊 2：Cookie
# ============================================================

st.divider()
st.header("🍪 Cookie 設定")

cookie_current = fp.load_cookie()
c_cookie, c_btn = st.columns([4, 1])

with c_cookie:
    if st.session_state.show_cookie:
        ci = st.text_input("BUFF Cookie", value=cookie_current,
                          placeholder="csrf_token=...; session=...", label_visibility="collapsed")
        if st.button("✅ 確定", use_container_width=True):
            if ci.strip():
                ok, msg = fp.save_cookie_to_config(ci.strip())
                st.session_state.save_msg = (ok, msg)
                st.session_state.show_cookie = False
                st.rerun()
    else:
        st.markdown("🍪 " + ("✅ 已設定" if cookie_current else "⚠️ 未設定"))

with c_btn:
    if st.button("✏️ 編輯 Cookie", use_container_width=True):
        st.session_state.show_cookie = not st.session_state.show_cookie
        st.rerun()

# ============================================================
# 區塊 3：BUFF 價格資料庫（折疊）
# ============================================================

st.divider()

# 同步狀態（常態顯示）
db_path = fp.DB_PATH
df_exists = os.path.exists(db_path) and read_db_as_dataframe(db_path) is not None
if df_exists:
    df_check = read_db_as_dataframe(db_path)
    item_count = len(df_check)
    last_update = df_check["最後更新時間"].max() if not df_check.empty else "從未"
    st.markdown(f"📊 **BUFF 價格資料庫** — {item_count} 種皮膚　|　最後更新：{last_update}")
else:
    st.markdown(f"📊 **BUFF 價格資料庫** — 尚未同步")

col_sync_status, col_sync_btn = st.columns([3, 1])
with col_sync_status:
    if "last_sync_time" in st.session_state and st.session_state.last_sync_time:
        st.info(f"✅ 今日已同步（{st.session_state.last_sync_time}）")
    else:
        st.info("⏳ 今日尚未同步")

with col_sync_btn:
    now = datetime.now()
    cooldown_ok = True
    if "last_sync_click" in st.session_state:
        elapsed = (now - st.session_state.last_sync_click).seconds
        if elapsed < 600:
            cooldown_ok = False
            remaining = 600 - elapsed
            st.button(f"⏳ 冷卻 {remaining//60}:{remaining%60:02d}", disabled=True, use_container_width=True)
    if cooldown_ok:
        if st.button("🚀 手動同步", use_container_width=True, disabled=st.session_state.sync_running):
            cookie = fp.load_cookie()
            if not cookie:
                st.error("❌ 請先設定 BUFF Cookie！")
            else:
                st.session_state.last_sync_click = now
                st.session_state.sync_running = True
                st.rerun()

# 同步執行邏輯
if st.session_state.sync_running:
    cookie = fp.load_cookie()
    sp = st.status("正在查詢皮膚價格...", expanded=True)
    pb = st.progress(0, text="準備中...")
    rc = st.container()
    def on_progress(curr, total, name, txt):
        cn = fp.SKIN_CN.get(name, name)
        pb.progress(curr / total, text=f"[{curr}/{total}] {cn}")
        rc.text(f"  {curr}/{total}  {cn}  →  {txt}")
    try:
        inserted, total_count, _ = fp.run_sync(cookie, progress_callback=on_progress)
        sp.update(label=f"✅ 完成！成功 {inserted}/{total_count} 筆", state="complete", expanded=False)
        pb.progress(1.0, text="完成")
        st.session_state.last_sync_time = datetime.now().strftime("%H:%M")
        st.session_state.sync_result = {"success": inserted, "total": total_count}
    except Exception as e:
        sp.update(label=f"❌ 失敗：{e}", state="error")
        st.session_state.sync_result = {"error": str(e)}
    st.session_state.sync_running = False
    st.rerun()

# 資料庫表格（折疊）
with st.expander("📦 查看完整價格表"):
    df = read_db_as_dataframe(db_path)
    if not df.empty:
        col_ref, col_info = st.columns([1, 5])
        with col_ref:
            st.button("🔄 重新整理", use_container_width=True, key="refresh_db")
        with col_info:
            a1, a2, a3 = st.columns(3)
            a1.metric("📦 皮膚數量", f"{len(df)} 個")
            a2.metric("💰 平均價格", f"NT${df['台幣價格'].mean():,.0f}")
            a3.metric("📈 總成交量", f"{df['成交量'].sum():,} 件")

        gdf = build_grouped_df(df)
        if gdf is not None:
            dc, co2 = {}, ["基底名稱", "中文名稱"]
            for col in gdf.columns:
                if col.startswith("price_"):
                    ws = col.replace("price_", ""); dc[col] = st.column_config.TextColumn(f"💰 {ws}", width="small")
                    gdf[col] = gdf[col].apply(lambda x: f"NT${x:,.0f}" if pd.notna(x) else "—"); co2.append(col)
                elif col.startswith("vol_"):
                    ws = col.replace("vol_", ""); dc[col] = st.column_config.TextColumn(f"📦 {ws}", width="small")
                    gdf[col] = gdf[col].apply(lambda x: f"{x:,.0f}" if pd.notna(x) else ""); co2.append(col)
                elif col == "基底名稱": dc[col] = st.column_config.TextColumn("皮膚名稱", width="medium")
                elif col == "中文名稱": dc[col] = st.column_config.TextColumn("中文名稱 (BUFF)", width="medium")
            st.dataframe(gdf[co2], width="stretch", column_config=dc, hide_index=True)
        csv = df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(label="📥 下載 CSV", data=csv, file_name=f"cs2_prices_{datetime.now().strftime('%Y%m%d_%H%M')}.csv", mime="text/csv")
    else:
        st.info("📭 資料庫為空")

# ============================================================
# 頁尾
# ============================================================

st.divider()
st.caption("CS2 搬磚價格查詢工具 ｜ BUFF 163 ｜ config.txt ｜ cs2_prices.db")
