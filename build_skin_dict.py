"""
build_skin_dict.py — 建立三層合併的中英皮膚對照表 (v2: id mapping)
========================================================
來源:
  1. CSGO-API (ByMykel) — 每週自動更新, 用 **id mapping** 建立簡中→英文
     (id 固定不變, 比名字比對可靠)
  2. SwaneyT 翻譯檔 — 2856 筆完整對照
  3. FB 社團俗稱 — 手動維護

輸出:
  skin_dict.json {
    full_cn_to_en: 完整名稱對照 (簡中官方名 → 英文名),
    pattern_cn_to_en: 花紋對照 (中文花紋 → 英文花紋),
    slang_cn_to_en: FB 俗稱
  }
"""
import json
import os
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "skin_dict.json")

API = "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/{lang}/skins.json"


def fetch(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=120).read().decode())


def extract_pattern(name: str) -> str:
    """從完整名稱取花紋部分 (| 後面)"""
    if '|' in name:
        return name.split('|')[1].strip()
    return name.strip()


def download_csgo_api():
    """用 id mapping 建立簡中→英文對照 (最可靠)"""
    en_data = fetch(API.format(lang="en"))
    cn_data = fetch(API.format(lang="zh-CN"))

    # id → name
    en_by_id = {s["id"]: s["name"] for s in en_data}
    cn_by_id = {s["id"]: s["name"] for s in cn_data}

    full_map = {}    # 簡中完整名 → 英文完整名
    pattern_map = {}  # 簡中花紋 → 英文花紋

    for sid, cn_name in cn_by_id.items():
        en_name = en_by_id.get(sid)
        if not en_name or not cn_name:
            continue
        full_map[cn_name] = en_name
        cn_pat = extract_pattern(cn_name)
        en_pat = extract_pattern(en_name)
        if cn_pat and en_pat and cn_pat != en_pat:
            pattern_map[cn_pat] = en_pat

    return full_map, pattern_map


def fetch_text(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    return urllib.request.urlopen(req, timeout=120).read().decode("utf-8")


def download_swaneyt():
    """下載 SwaneyT 中英翻譯檔 (純文字格式)"""
    url = ("https://raw.githubusercontent.com/SwaneyT/"
           "cs2-items-chinese-to-english-translation/main/"
           "cs2_chinese_english_translation_skins_no_wear.txt")
    content = fetch_text(url)

    full_map = {}
    for line in content.splitlines():
        line = line.strip().rstrip(',')
        if ':' not in line:
            continue
        try:
            parts = line.split("': '")
            cn = parts[0].lstrip("'").strip()
            en = parts[1].rstrip("'").strip()
            if cn and en:
                full_map[cn] = en
        except Exception:
            continue
    return full_map


def main():
    print("=== 建立合併字典 (v2 id mapping) ===")

    print("[1/4] 下載 CSGO-API (id mapping)...")
    api_full, api_pattern = download_csgo_api()
    print(f"     完整名 {len(api_full)} 筆, 花紋 {len(api_pattern)} 筆")

    print("[2/4] 下載 SwaneyT...")
    swaneyt_full = download_swaneyt()
    print(f"     完整名 {len(swaneyt_full)} 筆")

    print("[3/4] 讀取既有字典...")
    existing = {}
    if os.path.exists(OUT):
        with open(OUT, "r", encoding="utf-8") as f:
            existing = json.load(f)
    print(f"     既有花紋 {len(existing.get('pattern_cn_to_en', {}))} 筆")

    print("[4/4] 合併...")
    full_cn_to_en = {}
    full_cn_to_en.update(swaneyt_full)
    full_cn_to_en.update(api_full)       # CSGO-API 較新，覆蓋
    full_cn_to_en.update(existing.get("full_cn_to_en", {}))  # 手動俗稱

    pattern_cn_to_en = {}
    pattern_cn_to_en.update(api_pattern)
    pattern_cn_to_en.update(existing.get("pattern_cn_to_en", {}))  # 手動覆蓋

    merged = {
        "full_cn_to_en": full_cn_to_en,
        "pattern_cn_to_en": pattern_cn_to_en,
        "slang_cn_to_en": existing.get("slang_cn_to_en", {}),
        "meta": {
            "sources": ["ByMykel/CSGO-API id-mapping (每週更新)", "SwaneyT (2024)", "手動FB俗稱"],
            "full_count": len(full_cn_to_en),
            "pattern_count": len(pattern_cn_to_en),
        }
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成!")
    print(f"   完整名稱對照: {len(full_cn_to_en)} 筆")
    print(f"   花紋對照: {len(pattern_cn_to_en)} 筆")

    # 驗證
    for cn in ["阿西莫夫", "暴怒野兽", "怒火兽心", "火灵纹阵", "妖灵格栅",
                "迈阿密风云", "深红之网", "印花集"]:
        en = pattern_cn_to_en.get(cn, "❌ 缺")
        print(f"   驗證 {cn}: {en}")


if __name__ == "__main__":
    main()
