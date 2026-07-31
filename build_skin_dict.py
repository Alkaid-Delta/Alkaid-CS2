"""
build_skin_dict.py — 建立三層合併的中英皮膚對照表
來源:
  1. CSGO-API (ByMykel) — 每週自動更新, 官方簡中→英文
  2. SwaneyT 翻譯檔 — 2856 筆完整對照
  3. FB 社團俗稱 — 手動維護 (existing skin_dict.json)
輸出:
  skin_dict.json {pattern_cn_to_en, full_cn_to_en, slang_cn_to_en}
"""
import json
import os
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(BASE, "skin_dict.json")


def extract_pattern(name: str) -> str:
    """從完整名稱取花紋部分 (| 後面)"""
    if '|' in name:
        return name.split('|')[1].strip()
    return name.strip()


def download_csgo_api():
    """從 CSGO-API 下載最新中英對照 (每週更新)"""
    urls = {
        "en": "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/en/inventory.json",
        "cn": "https://raw.githubusercontent.com/ByMykel/CSGO-API/main/public/api/zh-CN/inventory.json",
    }
    data = {}
    for lang, url in urls.items():
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        data[lang] = json.loads(urllib.request.urlopen(req, timeout=120).read().decode())

    full_map = {}   # 中文完整名 → 英文完整名
    pattern_map = {}  # 中文花紋 → 英文花紋
    for skin_id, wears in data["en"]["skins"].items():
        cn_wears = data["cn"]["skins"].get(skin_id, {})
        for wear_id, en_info in wears.items():
            cn_info = cn_wears.get(wear_id, {})
            en_full = en_info.get("name", "")
            cn_full = cn_info.get("name", "")
            if not en_full or not cn_full:
                continue
            full_map[cn_full] = en_full
            en_pat = extract_pattern(en_full)
            cn_pat = extract_pattern(cn_full)
            if en_pat and cn_pat and cn_pat != en_pat:
                pattern_map[cn_pat] = en_pat

    # 也加入完整名的最後一段 (花紋) 對照
    return full_map, pattern_map


def download_swaneyt():
    """下載 SwaneyT 中英翻譯檔 (2856筆)"""
    url = ("https://raw.githubusercontent.com/SwaneyT/"
           "cs2-items-chinese-to-english-translation/main/"
           "cs2_chinese_english_translation_skins_no_wear.txt")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    content = urllib.request.urlopen(req, timeout=30).read().decode("utf-8")

    full_map = {}
    for line in content.splitlines():
        line = line.strip().rstrip(',')
        if ':' not in line:
            continue
        try:
            # 格式: '中文名': '英文名'
            parts = line.split("': '")
            cn = parts[0].lstrip("'").strip()
            en = parts[1].rstrip("'").strip()
            if cn and en:
                full_map[cn] = en
        except Exception:
            continue
    return full_map


def main():
    print("=== 建立合併字典 ===")

    # 1. CSGO-API (最新)
    print("[1/4] 下載 CSGO-API...")
    api_full, api_pattern = download_csgo_api()
    print(f"     完整名 {len(api_full)} 筆, 花紋 {len(api_pattern)} 筆")

    # 2. SwaneyT
    print("[2/4] 下載 SwaneyT...")
    swaneyt_full = download_swaneyt()
    print(f"     完整名 {len(swaneyt_full)} 筆")

    # 3. 既有字典 (含 FB 俗稱)
    print("[3/4] 讀取既有字典...")
    existing = {}
    if os.path.exists(OUT):
        with open(OUT, "r", encoding="utf-8") as f:
            existing = json.load(f)
    old_pattern = existing.get("pattern_cn_to_en", {})
    print(f"     既有花紋 {len(old_pattern)} 筆")

    # 4. 合併
    print("[4/4] 合併...")
    full_cn_to_en = {}
    full_cn_to_en.update(swaneyt_full)   # SwaneyT 優先? 不, CSGO-API 更新
    full_cn_to_en.update(api_full)       # CSGO-API 覆蓋 (更新)
    full_cn_to_en.update(existing.get("full_cn_to_en", {}))  # 手動

    pattern_cn_to_en = {}
    pattern_cn_to_en.update(api_pattern)
    pattern_cn_to_en.update(old_pattern)  # 手動俗稱覆蓋官方

    merged = {
        "full_cn_to_en": full_cn_to_en,
        "pattern_cn_to_en": pattern_cn_to_en,
        "slang_cn_to_en": existing.get("slang_cn_to_en", {}),
        "meta": {
            "sources": ["ByMykel/CSGO-API (每週更新)", "SwaneyT (2024)", "手動FB俗稱"],
            "full_count": len(full_cn_to_en),
            "pattern_count": len(pattern_cn_to_en),
        }
    }

    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(merged, f, ensure_ascii=False, indent=2)

    print(f"\n✅ 完成!")
    print(f"   完整名稱對照: {len(full_cn_to_en)} 筆")
    print(f"   花紋對照: {len(pattern_cn_to_en)} 筆")
    print(f"   輸出: {OUT}")

    # 驗證
    for cn, en in [("火灵纹阵", "?"), ("妖灵格栅", "?"), ("迈阿密风云", "?"),
                    ("深红之网", "?"), ("印花集", "?")]:
        print(f"   驗證 {cn}: {pattern_cn_to_en.get(cn, '❌ 缺')}")


if __name__ == "__main__":
    main()
