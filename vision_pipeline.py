"""
vision_pipeline.py — 生產級 Vision 管線：切格子 + 並行 + 交叉驗證 + 查價
========================================================
核心概念：
1. 大圖切成網格 (Grid Crop) → 每格單獨分析，微小特徵(黃勾)辨識率大幅提升
2. asyncio.Semaphore 並行控制 → 避免 API 429
3. 信心度低於閾值 → 觸發第二次交叉驗證 (Self-Consistency)
4. 只有勾選的項目才查價 → 省 API 費用
"""
import asyncio
import io
import logging
import os
import sys
from typing import List, Dict, Any, Optional, Tuple

from PIL import Image

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger("VisionPipeline")

# 設定 API Key
os.environ.setdefault("OPENROUTER_API_KEY", "")
os.environ.setdefault("DEEPSEEK_API_KEY", "")


class VisionAnalysisPayload:
    """單一網格的影像資料"""
    def __init__(self, item_id: int, image_bytes: bytes, coords: Tuple[int, int, int, int]):
        if not image_bytes:
            raise ValueError("影像二進位資料不可為空")
        self.item_id = item_id
        self.image_bytes = image_bytes
        self.coords = coords  # (left, upper, right, lower)


class VisionPipelineResult:
    """單一網格的分析結果"""
    def __init__(self, item_id: int, name: str = "", is_checked: bool = False,
                 confidence: float = 0.0, price: float = 0.0, wear: str = "",
                 currency: str = "RMB", success: bool = True, error_msg: str = ""):
        self.item_id = item_id
        self.name = name
        self.is_checked = is_checked
        self.confidence = confidence
        self.price = price
        self.wear = wear
        self.currency = currency
        self.success = success
        self.error_msg = error_msg

    def to_dict(self) -> Dict[str, Any]:
        return self.__dict__


class HermesVisionProcessor:
    def __init__(self, max_concurrent_tasks: int = 4, confidence_threshold: float = 0.7):
        self.semaphore = asyncio.Semaphore(max_concurrent_tasks)
        self.confidence_threshold = confidence_threshold

    # ── 圖片切割（含重疊，避免黃勾被切在邊界）──
    def slice_image(self, image_bytes: bytes, rows: int, cols: int,
                    overlap: float = 0.15) -> List[VisionAnalysisPayload]:
        """將大圖切成網格，每格獨立分析。overlap=相鄰格重疊比例，避免特徵被切斷"""
        payloads = []
        with Image.open(io.BytesIO(image_bytes)) as img:
            img_width, img_height = img.size
            base_w = img_width // cols
            base_h = img_height // rows
            ov_w = int(base_w * overlap)
            ov_h = int(base_h * overlap)

            item_id = 0
            for r in range(rows):
                for c in range(cols):
                    left = max(0, c * base_w - ov_w)
                    upper = max(0, r * base_h - ov_h)
                    right = min(img_width, (c + 1) * base_w + ov_w)
                    lower = min(img_height, (r + 1) * base_h + ov_h)

                    cropped = img.crop((left, upper, right, lower))
                    # 放大格子，讓小字更清楚（2 倍）
                    cropped = cropped.resize((cropped.width * 2, cropped.height * 2),
                                             Image.LANCZOS)
                    buf = io.BytesIO()
                    cropped.save(buf, format='PNG')

                    payloads.append(VisionAnalysisPayload(
                        item_id=item_id,
                        image_bytes=buf.getvalue(),
                        coords=(left, upper, right, lower)
                    ))
                    item_id += 1

        logger.info(f"✅ 圖像分割完成: {len(payloads)} 格 ({rows}x{cols}, overlap={overlap})")
        return payloads

    # ── Vision LLM 呼叫（OpenRouter Gemini）──
    async def _call_vision_llm(self, image_bytes: bytes) -> Dict[str, Any]:
        """呼叫 Vision API 分析單一網格：名稱 + 黃勾 + 信心度"""
        import vision_analyzer as va
        loop = asyncio.get_event_loop()

        def sync_call():
            return va.analyze_image(
                image_bytes,
                custom_prompt=(
                    "這是 CS2 BUFF 庫存截圖的其中一格。\n"
                    "請判斷：\n"
                    "1. name: 物品的完整中文名稱（含武器類型+花紋，如 AK-47 | 红线）\n"
                    "   ⚠️ 注意：黃色勾勾可能遮住花紋名稱文字，請**忽略黃色標記**，\n"
                    "      專注讀取物品本身的文字（通常名稱是白色小字）\n"
                    "2. wear: 磨損度（崭新/略有磨损/久经/破损/战痕）\n"
                    "3. is_checked: 這格是否有**明顯的黃色勾勾標記**？true/false\n"
                    "   注意：手繪黃勾通常是粗黃線畫的✓，顏色明顯偏黃\n"
                    "4. confidence: 你對 is_checked 判斷的信心（0.0-1.0）\n"
                    "5. price: 物品價格數字（無=0）\n"
                    "6. currency: RMB 或 TWD\n"
                    "只回傳 JSON: "
                    '{"name":"...","wear":"...","is_checked":true,"confidence":0.9,"price":4695,"currency":"RMB"}'
                ),
                retry=1
            )

        try:
            result = await loop.run_in_executor(None, sync_call)
            if not result:
                return {"name": "", "wear": "", "is_checked": False,
                        "confidence": 0.0, "price": 0, "currency": "RMB"}
            # vision_analyzer 回傳可能是 dict 或 list
            if isinstance(result, list):
                result = result[0] if result else {}
            return {
                "name": result.get("name", result.get("chinese_name", "")),
                "wear": result.get("wear", ""),
                "is_checked": bool(result.get("is_checked", False)),
                "confidence": float(result.get("confidence", 0.0)),
                "price": float(result.get("price", 0) or 0),
                "currency": result.get("currency", "RMB"),
            }
        except Exception as e:
            logger.error(f"Vision 呼叫失敗: {e}")
            return {"name": "", "wear": "", "is_checked": False,
                    "confidence": 0.0, "price": 0, "currency": "RMB"}

    # ── 查價（openskin API）──
    async def _call_pricing_api(self, name: str, wear: str) -> float:
        """查詢 BUFF 價格（透過 csgoskins_bridge 的 openskin API）"""
        if not name:
            return 0.0
        import csgoskins_bridge as cb
        loop = asyncio.get_event_loop()

        # 組出完整名稱
        wear_en = {
            "崭新": "Factory New", "嶄新": "Factory New",
            "略有磨损": "Minimal Wear", "略有磨損": "Minimal Wear",
            "久经": "Field-Tested", "久經": "Field-Tested",
            "破损": "Well-Worn", "破損": "Well-Worn",
            "战痕": "Battle-Scarred", "戰痕": "Battle-Scarred",
        }.get(wear, "Field-Tested")
        full_name = f"{name} ({wear_en})"

        def sync_call():
            r = cb.fetch_buff_price(full_name)
            return r["price_rmb"] if r else 0.0

        try:
            return await loop.run_in_executor(None, sync_call)
        except Exception:
            return 0.0

    # ── 單格分析流水線 ──
    async def analyze_single_chunk(self, payload: VisionAnalysisPayload) -> VisionPipelineResult:
        async with self.semaphore:
            try:
                # 第一次分析
                raw = await self._call_vision_llm(payload.image_bytes)
                name = raw.get("name", "")
                is_checked = raw.get("is_checked", False)
                confidence = raw.get("confidence", 0.0)
                wear = raw.get("wear", "")

                # 交叉驗證：信心度不足 → 再問一次
                if confidence < self.confidence_threshold:
                    logger.warning(f"[格{payload.item_id}] 信心度{confidence:.2f}過低，交叉驗證...")
                    retry = await self._call_vision_llm(payload.image_bytes)
                    # 兩次 is_checked 一致才採信
                    if retry.get("is_checked") == is_checked:
                        confidence = max(confidence, retry.get("confidence", 0.0))
                        if retry.get("name"):
                            name = retry.get("name")
                    else:
                        # 衝突 → 保守處理：以「有勾」的版本為準但降信心
                        is_checked = retry.get("is_checked", is_checked)
                        confidence = min(confidence, retry.get("confidence", confidence))

                # 名稱交叉驗證：兩次讀出的名稱不一致 → 名稱不可靠 → 跳過（不勉強）
                if is_checked and name:
                    retry_name = await self._call_vision_llm(payload.image_bytes)
                    n2 = retry_name.get("name", "")
                    # 名稱每次都不一樣 → 無法信賴，跳過此格
                    if n2 and n2 != name and not (n2[:4] in name or name[:4] in n2):
                        logger.warning(f"[格{payload.item_id}] 名稱不穩定({name} vs {n2})，跳過")
                        return VisionPipelineResult(
                            payload.item_id, name, is_checked, confidence,
                            success=False, error_msg="Unstable name, skipped"
                        )

                # 信心度仍不足 → 跳過（省查價費用）
                if confidence < self.confidence_threshold:
                    return VisionPipelineResult(
                        payload.item_id, name, is_checked, confidence,
                        success=False, error_msg="Low confidence bypass"
                    )

                # 只有勾選的才查價
                price = 0.0
                if is_checked:
                    price = await self._call_pricing_api(name, wear)

                return VisionPipelineResult(
                    payload.item_id, name, is_checked, confidence,
                    price=price, wear=wear,
                    currency=raw.get("currency", "RMB")
                )

            except Exception as e:
                logger.error(f"[格{payload.item_id}] 異常: {e}")
                return VisionPipelineResult(
                    payload.item_id, "", False, 0.0,
                    success=False, error_msg=str(e)
                )

    # ── 主入口 ──
    async def execute_pipeline(self, image_bytes: bytes, rows: int, cols: int) -> List[Dict[str, Any]]:
        chunks = self.slice_image(image_bytes, rows, cols)
        tasks = [self.analyze_single_chunk(chunk) for chunk in chunks]
        results = await asyncio.gather(*tasks)
        return [res.to_dict() for res in results]


def run_pipeline_sync(image_bytes: bytes, rows: int = 7, cols: int = 3,
                      max_concurrent: int = 4, threshold: float = 0.7) -> List[Dict[str, Any]]:
    """同步包裝：直接呼叫即可"""
    processor = HermesVisionProcessor(max_concurrent_tasks=max_concurrent,
                                      confidence_threshold=threshold)
    return asyncio.run(processor.execute_pipeline(image_bytes, rows, cols))


if __name__ == "__main__":
    # 測試
    import requests

    test_url = ("https://scontent.ftpe8-4.fna.fbcdn.net/v/t39.30808-6/759942024_28603518529250622_8616623026138522928_n.jpg"
                "?stp=cp6_dst-jpg_tt6&cstp=mx630x960&ctp=s630x960&_nc_cat=110&ccb=1-7&_nc_sid=aa7b47"
                "&_nc_ohc=uQZutGA7aaoQ7kNvwFxjeHX&_nc_oc=AdpGWXmZ7IpFvFo0WemO7x5sXUqx0C_OTMLcM-LTkHsGCY2NmytL7FJy6Vhy9LjjdH67a2gBS8zHdpGVjZnIyVm9"
                "&_nc_zt=23&_nc_ht=scontent.ftpe8-4.fna&_nc_gid=WfMY9VhCl6nFtcedmk9Bmw&_nc_ss=7b2a8"
                "&oh=00_AQEQmdkCp16i-An5qjP7JS9amK6eBRg4vIbHOHBrL4KpIw&oe=6A724342")
    resp = requests.get(test_url, timeout=30)
    print(f"✅ 下載圖片: {len(resp.content)} bytes")

    results = run_pipeline_sync(resp.content, rows=7, cols=3, max_concurrent=4)
    print("\n=== 管線結果 ===")
    for r in results:
        mark = "✔" if r["is_checked"] else " "
        print(f"  [{mark}] 格{r['item_id']:2d} conf={r['confidence']:.2f} "
              f"{r['name'][:35]:35s} {r['wear']:6s} ¥{r['price']:.2f}")
