# Alkaid-CS2 模型路由設計 — Flash + Pro 分層

> 狀態：設計階段（未修改程式、未 commit、未 push）
> 目標：Flash 為預設，複雜案例才升級 Pro，控制成本

---

## 0. 現況（已確認）

| 位置 | 模型 | 用途 |
|------|------|------|
| analyze_arbitrage.py L56/L61 | `deepseek-chat`（單一） | 皮膚提取、套利分析 |
| vision_analyzer.py L23 | `google/gemini-2.5-flash-image` | 圖片讀取 |

- **無分層**：所有文字抽取都用同一個模型
- **無升級機制**：失敗只重試一次同模型（L547），再失敗就回傳錯誤資料（L562）

---

## 1. 模型定義

```python
# model_router.py 開頭
MODEL_FLASH = os.environ.get("MODEL_FLASH", "deepseek-v4-flash")   # 預設
MODEL_PRO   = os.environ.get("MODEL_PRO",   "deepseek-v4-pro")     # 升級

MODEL_FLASH_ALIASES = ["deepseek/deepseek-chat", "deepseek-chat"]  # 相容
MODEL_PRO_ALIASES   = ["deepseek/deepseek-r1", "deepseek-reasoner"]
```

| 等級 | 用途 | 成本倍率 |
|------|------|---------|
| **Flash** | 預設：單商品、單圖、分類、JSON 抽取 | 1× |
| **Pro** | 升級：多商品、歧義、驗證失敗 | ~2-3×（估算） |

---

## 2. model_router.py 介面設計

```python
class ModelRouter:
    """Flash + Pro 分層路由，記錄每次解析"""

    def __init__(self, flash=MODEL_FLASH, pro=MODEL_PRO,
                 confidence_threshold=0.75, score_gap_threshold=0.2,
                 log_path="data/model_runs.jsonl"):
        ...

    # ── 4 個核心函式 ──

    def classify_complexity(self, post: ParsedPost) -> str:
        """
        回傳 "simple" | "complex"
        simple: 單一商品候選、單張圖、無價格歧義
        complex: 任一升級條件命中
        """

    def should_escalate_to_pro(self, post, candidates,
                               flash_result=None) -> tuple[bool, str]:
        """
        回傳 (是否升級, 升級原因)
        原因列舉: multi_item / multi_image / multi_skin_match /
                  price_mismatch / low_confidence / validation_failed /
                  close_scores / multi_price_type
        """

    def select_model(self, post, candidates, flash_result=None) -> str:
        """回傳要用的 model 名稱（flash 或 pro）"""

    def record_model_result(self, run_id, **fields) -> None:
        """每次解析寫一行 JSONL（見 §4）"""

    # ── 升級計數器（禁止無限重試）──
    def reset(self, run_id): ...
    def can_escalate(self, run_id) -> bool:
        """Flash 失敗後最多升 Pro 一次"""
```

### 2.1 classify_complexity() 判定規則

```python
def classify_complexity(self, post):
    n_items = len(post.items)
    n_images = len(post.image_urls)
    n_skin_hits = count_skin_matches(post.raw_text)   # 字典命中數
    n_price_types = count_price_types(post.prices)

    if n_items >= 2: return "complex"        # 多商品
    if n_images >= 2: return "complex"       # 多圖需整合
    if n_skin_hits >= 2: return "complex"    # 多皮膚命中
    if n_price_types >= 2: return "complex"  # 售價+參考+底價混雜
    return "simple"
```

---

## 3. 升級條件對照（8 條，全部由 Python 判斷）

| # | 條件 | 判斷處 | 觸發原因字串 |
|---|------|--------|-------------|
| 1 | 多商品貼文 | classify_complexity | `multi_item` |
| 2 | 多張圖片需整合 | classify_complexity | `multi_image` |
| 3 | 同時命中多個皮膚名稱 | classify_complexity | `multi_skin_match` |
| 4 | 商品與價格配對歧義 | price 綁定後檢查 | `price_mismatch` |
| 5 | Flash confidence < 0.75 | flash_result | `low_confidence` |
| 6 | 名稱驗證失敗 | _verify_skin_on_csgoskins | `validation_failed` |
| 7 | 候選分數差距 < 0.2 | 候選排序後 | `close_scores` |
| 8 | 貼文含售價+參考價+BUFF底 | price_type 統計 | `multi_price_type` |

**分數差距**（條件 7）：
```python
scores = sorted([c.score for c in candidates], reverse=True)
if len(scores) >= 2 and (scores[0] - scores[1]) < 0.2:
    escalate("close_scores")
```

---

## 4. 每次解析保存的記錄

```python
# data/model_runs.jsonl — 每行一筆
{
  "run_id": "p3_item1_20260801T123000",
  "timestamp": "2026-08-01T12:30:00",
  "post_id": "2934603960230855",
  "model_used": "deepseek-v4-flash",        # 最終使用的模型
  "escalation_reason": "validation_failed", # 升級原因（未升級=None）
  "escalation_count": 1,                    # 升級次數（0或1）
  "complexity": "complex",
  "candidates": [                           # 候選清單
    {"market_hash_name": "...", "role": "selling", "confidence": "high"}
  ],
  "confidence": 0.82,                       # 最終信心（0-1）
  "validation_status": "passed",            # passed/failed/unresolved/not_required
  "latency_ms": 1240,
  "token_usage": {"prompt": 320, "completion": 85, "total": 405},
  "final_result": {"market_hash_name": "...", "seller_price": 5000},
  "retry_count": 1
}
```

- 寫入：`record_model_result()`，JSONL append
- 讀取：`data/model_runs.jsonl` 供事後分析成本/品質

---

## 5. 升級流程（Flash 失敗 → Pro 一次）

```
extract_skin_candidates(post)
  │
  ├─ classify_complexity(post)
  │     └─ complex → 直接 select_model=PRO（首次就用 Pro）
  │
  ├─ simple → Flash 執行
  │     ├─ 成功 + confidence≥0.75 + 驗證通過 → ✅ 完成
  │     ├─ confidence < 0.75 → 升級 Pro（原因 low_confidence）
  │     ├─ 驗證失敗 → 升級 Pro（原因 validation_failed）
  │     └─ 失敗（exception）→ 升級 Pro（原因 flash_error）
  │
  └─ Pro 執行
        ├─ 成功 → ✅ 完成
        └─ 失敗 → ❌ unresolved（禁止第 3 次）
```

**禁止無限重試**：
```python
if router.can_escalate(run_id) is False:
    return None  # 已升過 Pro，不再重試 → unresolved
```

---

## 6. 禁止模型處理的清單

| 禁止事項 | 由誰決定 | 現況 |
|---------|---------|------|
| 匯率換算（RMB→TWD） | **currency_service.py** | ⚠️ 目前 DeepSeek 會換算（L517）→ 移除 |
| 手續費計算（BUFF 5%） | **analyze_arbitrage.py**（BUFF_FEE_RATE） | ✅ 已是 Python |
| 最終利潤計算 | **analyze_arbitrage.py** | ✅ 已是 Python |
| 資料庫驗證（csgoskins 404） | **_verify_skin_on_csgoskins()** | ✅ 已是 Python |

> prompt 明確寫：「你只負責輸出名稱與原始金額，禁止計算匯率、手續費、利潤。」

---

## 7. 成本影響估算

### 7.1 假設（DeepSeek v4 定價估算）
| 模型 | input/M tokens | output/M tokens | 倍率 |
|------|---------------|----------------|------|
| Flash | 基準 1× | 基準 1× | 1.0 |
| Pro | ~2.5× | ~3× | ~2.6 |

### 7.2 場景分佈（預估）
| 場景 | 佔比 | 模型 | 成本 |
|------|------|------|------|
| 單商品純文字（多數收/售） | 60% | Flash | 1× |
| 單商品圖片 | 25% | Flash | 1× |
| 多商品/多圖/歧義 | 12% | Pro | 2.6× |
| Flash 失敗升級 | 3% | Flash→Pro | 3.6×（2次呼叫） |

### 7.3 總成本
```
加權 = 0.60×1 + 0.25×1 + 0.12×2.6 + 0.03×3.6 ≈ 1.29×
```
→ 相比全 Pro（2.6×）**省約 50%**；相比現況單 Flash（1×）**增加約 29%**，但換來複雜案例的品質。

### 7.4 成本控制
- 複雜案例直接 Pro（不先 Flash 浪費一次）
- Flash 失敗只升一次（最多 2 次呼叫/貼文）
- 字典命中的候選**不走模型**（零成本）
- `token_usage` 記錄 → 可追蹤實際花費

---

## 8. 測試案例

```python
# tests/test_model_router.py

# R1: 單商品 → Flash
def test_simple_uses_flash():
    post = ParsedPost(items=[ItemCandidate(...)], image_urls=[])
    assert router.classify_complexity(post) == "simple"
    assert router.select_model(post, []) == MODEL_FLASH

# R2: 多商品 → Pro
def test_multi_item_uses_pro():
    post = ParsedPost(items=[c1, c2], image_urls=[])
    assert router.classify_complexity(post) == "complex"
    assert router.select_model(post, []) == MODEL_PRO

# R3: Flash confidence 0.6 → 升級 Pro
def test_low_confidence_escalates():
    flash_result = {"confidence": 0.6, "candidates": [...]}
    esc, reason = router.should_escalate_to_pro(post, [], flash_result)
    assert esc and reason == "low_confidence"

# R4: 名稱驗證失敗 → 升級 Pro
def test_validation_failed_escalates():
    esc, reason = router.should_escalate_to_pro(post, [], 
                    {"validation_status": "failed"})
    assert esc and reason == "validation_failed"

# R5: Flash 失敗 → Pro → 再失敗 → 停止
def test_max_one_escalation():
    router.reset("run1")
    assert router.can_escalate("run1") is True
    router.record_model_result("run1", escalation_count=1)
    assert router.can_escalate("run1") is False   # 升過一次就鎖

# R6: 候選分數差距 < 0.2 → 升級
def test_close_scores_escalates():
    c1 = ItemCandidate(score=0.80); c2 = ItemCandidate(score=0.65)
    esc, reason = router.should_escalate_to_pro(post, [c1, c2], {})
    assert esc and reason == "close_scores"

# R7: 記錄完整（8 欄位都在）
def test_record_complete():
    router.record_model_result("run7", model_used="flash", ...)
    line = read_last_line("data/model_runs.jsonl")
    for key in ["model_used", "escalation_reason", "candidates",
                "confidence", "validation_status", "latency_ms",
                "token_usage", "final_result"]:
        assert key in line

# R8: 匯率計算不經模型（模型只回原始金額）
def test_model_never_converts_currency():
    # 驗證 DeepSeek prompt 不含「乘4.5」字樣
    prompt = build_extract_prompt(...)
    assert "4.5" not in prompt
```

---

## 9. 修改檔案列表

### 新增
| 檔案 | 內容 |
|------|------|
| `model_router.py` | ModelRouter 類（4 核心函式 + 升級計數器） |
| `tests/test_model_router.py` | R1-R8 測試 |
| `docs/model-router-design.md` | 本文件 |
| `data/model_runs.jsonl` | 解析記錄（runtime 產生，gitignore） |

### 修改（增量）
| 檔案 | 修改點 |
|------|--------|
| `analyze_arbitrage.py` | L523/L547/L715 `model=MODEL` → `model=router.select_model(...)`；移除 prompt 中「乘4.5」（L517）；驗證失敗改走 router 升級而非原地重試 |
| `extractor.py`（P2 新增） | 呼叫 router.classify_complexity / should_escalate_to_pro |
| `.gitignore` | 加 `data/model_runs.jsonl` |

### 不修改
- `vision_analyzer.py`（gemini 維持，圖片分類不升 Pro）
- `cdp_fb_crawler.py`（圖片處理不涉及模型路由）

---

## 10. 風險與回滾

| 風險 | 緩解 |
|------|------|
| Pro 模型名稱不存在（deepseek-v4-pro 未設定） | `MODEL_PRO` 環境變數可覆蓋；缺省 fallback 到 flash |
| 升級判斷誤觸發（成本上升） | threshold 可調（confidence 0.75 / score_gap 0.2） |
| model_runs.jsonl 成長 | 每筆<1KB，10萬筆≈100MB，可定期輪替 |
| 舊流程行為改變 | model_router 預設全部走 Flash（升級條件全關 = 現況），逐步開啟 |

**回滾**：`git revert` 最後 commit；或環境變數 `MODEL_ROUTER_DISABLED=1` 直接回到單一 Flash。

---

## 11. 與資料模型改造的整合順序

```
P1: models.py + currency_service.py
P2: extractor.py（extract_skin_candidates）
P3: extract_skin_info 包裝（行為不變）
P4: 驗證 unresolved 修復
P5: 貨幣規則（prompt 不換算）
P6: crawler 收集全部圖
P7: model_router.py + 記錄（本設計）
P8: 整合測試
```

> model_router 在 P7 導入，不影響前面階段；Flash 預設行為 = 現況。
