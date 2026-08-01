# Alkaid-CS2 商品辨識資料模型 — 設計與測試方案

> 狀態：設計階段（未修改程式、未 commit、未 push）
> 目標：以增量修正解決 8 個已確認問題，不全面重構

---

## A. 現有資料流

### A.1 完整呼叫鏈

```
Facebook 社團頁面
   │
   │ (1) Playwright CDP 連線 + API 攔截
   ▼
cdp_fb_crawler.py: fetch_posts()
   │
   ├─ [API 層] _extract_posts_from_body(GraphQL response)
   │     輸入: group_feed 原始 JSON
   │     輸出: [{author, text, url, images}]
   │     ⚠️ text 常為空、images 常缺（FB API 限制）
   │
   ├─ [DOM 層] 滾動批次抓取 div[role=feed]>div
   │     輸入: 頁面 DOM
   │     輸出: [{dom_text, images[]}] (img srcset 高清)
   │     配對: dom_text[:15] in api_text → 補上 images
   │
   ├─ [圖片類型層] Vision 判斷 type (inventory/single/other)
   │     inventory → 跳過（規則：不勉強）
   │     single    → Vision 整圖讀 → items[]
   │
   ├─ [拆分層] 每張圖每個 item → 獨立候選 post
   │     輸出: [{id, author, content, link, currency}]
   │     ⚠️ 只取第一張成功的圖（break）
   │
   ▼
analyze_arbitrage.py: process_posts()
   │
   ├─ Step1: extract_skin_info(content)
   │     輸入: post.content (文字 or "[圖片] 售 ...")
   │     輸出: {market_hash_name, seller_price, confidence} | None
   │     ⚠️ 單一回傳、字典第一命中 return
   │
   ├─ Step2: lookup_buff_price(market_hash_name)
   │     輸入: 英文完整名稱
   │     輸出: {price_twd, volume} | None
   │     csgoskins_bridge: openskin API (USD) → RMB ×7.2 → TWD ×4.5
   │
   └─ Step3: analyze_arbitrage(post, buff_info)
         輸入: post + BUFF 基準價
         輸出: deal dict | None
```

### A.2 每層輸入/輸出格式

| 層 | 輸入 | 輸出 | 備註 |
|----|------|------|------|
| API 攔截 | GraphQL JSON | `[{author, text, url, images}]` | text/images 常缺 |
| DOM 補圖 | DOM | `{dom_text, images[]}` | 文字前15字比對配對 |
| 圖片類型 | 圖片 bytes | `{type, rows, cols}` | inventory/single/other |
| Vision 讀圖 | 圖片 bytes | `[{name, wear, price, currency}]` | 單一物品頁 |
| 拆分 | post + items | `[{id, author, content, link, currency}]` | 每 item 一筆 |
| extract_skin_info | content 字串 | `{mhn, seller_price, confidence} \| None` | **單一** |
| lookup_buff_price | mhn 字串 | `{price_twd, volume} \| None` | openskin |
| analyze_arbitrage | post + buff | deal \| None | DeepSeek 判定 |

### A.3 貨幣轉換位置（現況）

| 位置 | 轉換 | 問題 |
|------|------|------|
| **DeepSeek prompt** (analyze_arbitrage.py L517) | 要求 AI「RMB 自動 ×4.5 轉 TWD」 | ⚠️ AI 換算不可靠 |
| **process_posts** (L839-840) | `currency=="RMB" → ×4.5` | ⚠️ 若 DeepSeek 已換算，重複乘 |
| **csgoskins_bridge._try_openskin** (L99-101) | USD → ×7.2 RMB → ×4.5 TWD | ✅ 唯一正確處 |

---

## B. 新資料模型

```python
# ============ 角色列舉 ============
class ItemRole:
    SELLING   = "selling"    # 出售商品
    BUYING    = "buying"     # 收購/求購
    REFERENCE = "reference"  # 參考商品（底價、同磨底、對照用）
    UNKNOWN   = "unknown"

class PriceType:
    SELLER_ASK  = "seller_ask"   # 賣家開價
    REFERENCE   = "reference"    # 參考價（圖上/文中提及，非賣價）
    BUFF_FLOOR  = "buff_floor"   # BUFF 同磨底價
    CALCULATED  = "calculated"   # 由公式算出（如 底×4.4）
    UNKNOWN     = "unknown"

# ============ ParsedPost ============
@dataclass
class ParsedPost:
    post_id: str
    author: str
    link: str
    raw_text: str                    # 原始貼文文字（可能為空）
    image_urls: list[str]            # 全部圖片
    items: list["ItemCandidate"]     # 全部候選商品
    prices: list["PriceCandidate"]   # 全部候選價格
    parse_status: str                # ok / unresolved / skipped

# ============ ItemCandidate ============
@dataclass
class ItemCandidate:
    market_hash_name: str            # 完整英文名 (含 ★/StatTrak™/磨損)
    source_text: str                 # 產生此候選的原始文字片段
    weapon: str                      # 武器英文名 (AK-47 / Karambit...)
    skin: str                        # 花紋英文名 (Redline / Tiger Tooth)
    wear: str                        # Factory New / Minimal Wear / ...
    stattrak: bool                   # 是否暗金
    role: str                        # ItemRole.*
    confidence: str                  # high / medium / low
    evidence: str                    # 命中來源: dict_full / dict_pattern / deepseek / vision
    image_index: int                 # -1=無圖, 0..N=第幾張圖

# ============ PriceCandidate ============
@dataclass
class PriceCandidate:
    amount: float                    # 原始金額
    currency: str                    # RMB / TWD / USD
    amount_twd: float                # 經 currency service 換算後 TWD（未換算前為 None）
    price_type: str                  # PriceType.*
    source: str                      # text / image / calculation
    evidence: str                    # 原文片段或圖片索引
    associated_item_index: int       # 綁定的 ItemCandidate index（-1=未綁定）
```

### B.1 關聯規則
- `PriceCandidate.associated_item_index` → `ParsedPost.items[i]`
- 價格只綁定**最近**的商品文字區段（見 D.4）
- `role=reference` 的商品價格 → `price_type=reference`，**不得**當 seller_ask

---

## C. 相容性方案

### C.1 新增函式（新介面）
```python
def extract_skin_candidates(post: ParsedPost | dict) -> list[ItemCandidate]:
    """新流程：收集全部候選，排序後回傳清單"""
```

### C.2 保留舊介面（包裝層）
```python
def extract_skin_info(post_text: str) -> dict | None:
    """舊介面：內部改呼叫 extract_skin_candidates，取第一個 SELLING 候選"""
```

### C.3 過渡原則
- ✅ `extract_skin_info` 簽名不變 → 所有現有呼叫端（process_posts、測試）不動
- ✅ 不刪除任何現有功能（字典、DeepSeek、驗證、csgoskins 都保留）
- ⚠️ process_posts 暫不全面改，僅在 Step1 改用新函式產出候選
- 📌 逐步遷移：先讓 extract_skin_candidates 與 extract_skin_info 並行，跑測試對照結果一致後，再切換呼叫端

---

## D. 判斷規則

### D.1 完整名稱命中優先於花紋命中
```
查詢順序：
1. full_cn_to_en（完整名，如「AK-47 | 红线」）
2. pattern_cn_to_en（花紋名，如「红线」）+ weapon_map 拼裝
3. DeepSeek（fallback）
```
- 完整名命中的候選 confidence = high
- 花紋拼裝 confidence = high（有武器）或 medium（無武器）
- DeepSeek confidence = 依其自評

### D.2 長字串優先於短字串
```
「紅線」與「紅線行動」(Redline vs Redline Action 類) 同時存在時：
→ 先匹配更長、更完整的 key（len(cn) 大的優先）
```
- 避免短 key（如「虎牙」）誤吞長名稱（「虎牙（★）| ...」完整名）
- 排序：`sorted(candidates, key=lambda c: len(c.source_text), reverse=True)` 或先比 key 長度

### D.3 收集全部候選後再排序，不得第一命中 return
```python
candidates = []
for cn_full, en_full in full_dict.items():
    if cn_full in post_text:
        candidates.append(...)   # 收集，不 return
for cn, en in skin_dict.items():
    if cn in post_text and len(cn) >= 2:
        candidates.append(...)
# 全部收集完 → 去重 → 排序 → 依角色過濾
```
- 排序鍵：evidence 優先級（dict_full > dict_pattern > deepseek）→ 長度 → 位置
- 同文多候選（紅線 + 火神）→ **全部保留**，各自帶價格

### D.4 價格與最近的商品文字區段關聯
```
「14卡托紅線 7480 火神4xtitan 14000」
  → 區段1: 「紅線 7480」      → Price(7480, 綁 items[紅線])
  → 區段2: 「火神4xtitan 14000」 → Price(14000, 綁 items[火神])
```
- 實現：以花紋命中位置為錨點，向後掃描「最近數字」
- 或：DeepSeek 回傳時要求 `item_price_pairs`（名稱+價格成對），禁止分開回

### D.5 參考/底價/同磨底 不得視為 seller_ask
| 關鍵字 | 價格角色 |
|--------|---------|
| 同磨底 / 底4.3 / 底4.4 / BUFF底 | `buff_floor` 或 `reference` |
| 圖上 RMB 標價 | `reference`（BUFF 掛牌參考，非 FB 售價） |
| 售 / 賣 / 出 / 算 / 開價 / 帶走 | `seller_ask` |
| 其他數字 | `unknown`（低信心） |

### D.6 提高 selling/seller_ask 信心
- 命中「售、賣、出、算、開價」→ role=selling、price_type=seller_ask、confidence 升一級
- 命中「換、貼換、交換、想換」→ role=reference 或整篇標 skipped（交換非賣）
- 命中「收、徵、求購」→ role=buying

---

## E. 貨幣規則

### E.1 單一 Currency Service
```python
# 新增 currency_service.py（唯一換算處）
class CurrencyService:
    RMB_TO_TWD = 4.5
    USD_TO_RMB = 7.2
    def to_twd(self, amount, currency) -> float:
        """所有 RMB/USD→TWD 只能經過這裡"""
```
- 匯率集中管理，未來改動只動一處

### E.2 DeepSeek 只提取原始金額，不換算
- prompt 改為：「回傳原始 amount 與 currency（RMB/TWD/USD），**禁止自行換算**」
- 例：圖上「6120 RMB」→ 回 `{amount: 6120, currency: "RMB"}`，不乘 4.5

### E.3 禁止重複換算
- `PriceCandidate.amount_twd` 只有 currency service 能寫
- `process_posts` 不再做任何 `×4.5`（刪除 L839-840）
- 換算標記：`amount_twd is None` = 尚未換算；已換算 = 有值，任何人不得再乘

### E.4 保存原始幣別
- 每個 PriceCandidate 保留 `amount` + `currency`（原始值永遠可追溯）
- 圖片來源的 RMB 標價 → `price_type=reference`（不當售價）

---

## F. 驗證規則

### F.1 驗證失敗不得回傳未驗證資料
現況問題（L562 `return data`）：驗證失敗 → 重試失敗 → 回傳第一次未驗證名稱 ❌
```
修正：
  第1次 DeepSeek → mhn1 → 驗證失敗
  第2次重試    → mhn2 → 驗證失敗
  → 回傳 status="unresolved"，不得進入套利分析
```

### F.2 unresolved 處理
- `extract_skin_candidates` 回傳時標記 `confidence=low` + `status=unresolved`
- `process_posts` 遇到 unresolved → 印 `[1/3] ⚠️ 名稱驗證失敗(unresolved),跳過` → `continue`
- 字典命中的候選不需要 csgoskins 驗證（字典來自官方對照，可信任）
- 只有 DeepSeek 產生的候選需要驗證

---

## G. 測試案例（pytest）

```python
# 新增 tests/test_item_model.py

# T1: 同文含「紅線 AK」與「火神」，出售目標是紅線
def test_redline_preferred_over_vulcan():
    post_text = "出2把傳家寶ak 14卡托紅線 7480 火神4xtitan 14000"
    cands = extract_skin_candidates(post_text)
    selling = [c for c in cands if c.role == "selling"]
    # 斷言: 紅線與火神都在候選中
    names = [c.skin for c in selling]
    assert "Redline" in names and "Vulcan" in names
    # 斷言: 紅線價格綁 7480
    redline = [c for c in selling if c.skin == "Redline"][0]
    assert redline.associated_price.amount == 7480

# T2: 一篇貼文有兩件出售商品
def test_two_selling_items():
    post_text = "售 沙鷹鈷分裂 5000 收 AWP 電擊 3000"
    cands = extract_skin_candidates(post_text)
    selling = [c for c in cands if c.role == "selling"]
    assert len(selling) == 1  # 只有沙鷹是 selling，AWP 是 buying
    buying = [c for c in cands if c.role == "buying"]
    assert len(buying) == 1

# T3: 商品價格與參考 BUFF 底價同時存在
def test_seller_ask_vs_buff_floor():
    post_text = "售 久經邁阿密 同磨底2100*4.4=9200算5000"
    cands = extract_skin_candidates(post_text)
    prices = [p for c in cands for p in c.prices]
    ask = [p for p in prices if p.price_type == "seller_ask"]
    floor = [p for p in prices if p.price_type == "buff_floor"]
    assert ask[0].amount == 5000      # 算5000 = 賣價
    assert floor[0].amount == 2100    # 同磨底 2100 = 參考

# T4: 1000 RMB 只換算一次
def test_rmb_converted_once():
    svc = CurrencyService()
    p = PriceCandidate(amount=1000, currency="RMB", amount_twd=None, ...)
    p.amount_twd = svc.to_twd(1000, "RMB")   # 4500
    assert p.amount_twd == 4500
    # 已換算 → 不可再換
    with pytest.raises(AssertionError):
        p.amount_twd = svc.to_twd(p.amount_twd, "TWD")

# T5: 名稱驗證兩次失敗後不得進入套利分析
def test_unresolved_skipped(monkeypatch):
    monkeypatch.setattr("analyze_arbitrage._verify_skin_on_csgoskins", lambda m: False)
    post = {"id": "t5", "content": "售 神秘皮膚 5000", "link": ""}
    deals = process_posts([post])
    assert deals == []  # unresolved 不進入套利

# T6: 多張圖片第一張只有商品、第二張包含價格
def test_price_from_second_image():
    posts = fetch_posts_mock([
        {"id": "img1", "url": "...", "items": [{"name": "AK-47|红线", "wear": "FT"}]},   # 無價格
        {"id": "img2", "url": "...", "items": [{"name": "AK-47|红线", "wear": "FT", "price": 5000, "currency": "TWD"}]},  # 有價格
    ])
    # 斷言: 不再第一張就 break，第二張的價格有被合併
    merged = merge_candidates(posts)
    assert merged[0].prices[0].amount == 5000

# T7: 單商品舊流程仍可正常使用
def test_legacy_extract_skin_info():
    r = extract_skin_info("售 夜行衣 久經 5000")
    assert r is not None
    assert "Nocts" in r["market_hash_name"]
    assert r["seller_price"] == 5000  # 舊介面行為不變
```

---

## 建議檔案

### 新增
| 檔案 | 內容 |
|------|------|
| `models.py` | ParsedPost / ItemCandidate / PriceCandidate / ItemRole / PriceType |
| `currency_service.py` | 唯一匯率換算服務 |
| `extractor.py` | extract_skin_candidates()（收集+排序+角色判斷） |
| `tests/test_item_model.py` | 上述 7 個 pytest |
| `docs/data-model-redesign.md` | 本文件 |

### 修改（增量，不重構）
| 檔案 | 修改點 |
|------|--------|
| `analyze_arbitrage.py` | extract_skin_info 改包裝新函式；刪 L839-840 重複換算；修 L562 驗證失敗回傳；prompt 改「不換算」 |
| `cdp_fb_crawler.py` | 圖片處理收集全部圖（移除第一張 break）；輸出帶 currency + image_index |
| `vision_analyzer.py` | 輸出加 image_index（無程式碼大改） |

---

## 風險

| 風險 | 影響 | 緩解 |
|------|------|------|
| 字典迴圈改「全收集」後效能下降 | full_dict 5355 筆 × 每貼文 | 只掃描 len(cn)≥2 的 key，先過濾 |
| 舊介面行為改變（價格綁定變嚴） | 部分原本「矇對」的貼文變 unresolved | 對照測試，T7 保證單商品流程不變 |
| 移除 DeepSeek 換算後價格來源變少 | 純文字 RMB 貼文無價格 | currency service 統一處理，圖片 RMB 標 reference |
| 角色判斷誤判（換/賣） | 交換文誤判賣家 | 交換詞黑名單 + 無文字貼文標 unknown |
| process_posts 半遷移狀態 | 新舊資料結構混用 | ParsedPost 欄位 optional，相容 dict |

## 回滾方案
- 所有修改皆為**新增檔案 + 函式內增量**，git 分階段 commit
- 若新流程有誤：`git revert <last-commit>` 即可回到舊流程
- extract_skin_info 舊介面全程保留，隨時可切回
- currency_service 為純新增，不影響舊路徑

## 分階段 commit 計畫

| 階段 | 內容 | 驗證 |
|------|------|------|
| **P1** | 新增 models.py + currency_service.py + 單元測試(T4) | pytest 通過 |
| **P2** | 新增 extractor.py：extract_skin_candidates()（收集+排序+角色），T1/T2/T3 | pytest 通過 |
| **P3** | extract_skin_info 改包裝新函式（行為不變），T7 | 舊測試通過 |
| **P4** | 修 F.1：驗證失敗回傳 unresolved，T5 | pytest 通過 |
| **P5** | 修 E：prompt 不換算 + 刪 process_posts 重複換算 | T4 + 實際貼文對照 |
| **P6** | crawler：收集全部圖（移除 break），T6 | 實測多圖貼文 |
| **P7** | 整合測試：完整 FB→分析流程 | 與現況結果對照 |

每階段獨立 commit，可單獨 revert。全程不 push 直到你確認。
