"""
vision_analyzer.py — CS2 交易截圖 Vision AI 分析模組
=====================================================
使用 OpenRouter + Gemini 2.5 Flash Image 讀取 FB 社團貼文截圖，
自動辨識皮膚名稱、磨損度、價格等資訊。

支援兩種模式：
  1. 直接分析圖片 bytes（從 Playwright 截圖）
  2. 分析圖片檔案路徑
"""

import os
import sys
import json
import base64
import requests
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---- OpenRouter 設定 ----
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
VISION_MODEL = "google/gemini-2.5-flash-image"
API_URL = "https://openrouter.ai/api/v1/chat/completions"


def _encode_image(image_bytes: bytes) -> str:
    """將圖片 bytes 轉為 base64。"""
    return base64.b64encode(image_bytes).decode("utf-8")


def analyze_image(image_bytes: bytes, retry: int = 2, custom_prompt: str | None = None) -> dict | list | None:
    """分析一張 CS2 交易截圖，提取皮膚價格資訊。

    Args:
        image_bytes: 圖片原始 bytes（PNG/JPG）。
        retry: 失敗重試次數。
        custom_prompt: 自訂提示詞。若提供，則完全取代預設的 user_prompt。

    Returns:
        預設模式回傳 dict；若 custom_prompt 指定陣列格式則回傳 list。
        失敗回傳 None。
    """
    if not OPENROUTER_API_KEY:
        print("  [Vision] ❌ 未設定 OPENROUTER_API_KEY")
        return None

    b64 = _encode_image(image_bytes)
    data_uri = f"data:image/png;base64,{b64}"

    system_prompt = (
        "你是一個專業的 CS2 交易截圖分析助手。"
        "請仔細閱讀圖片中的文字，提取 CS2 皮膚交易資訊。"
    )

    user_prompt = custom_prompt if custom_prompt else (
        "請分析這張 CS2 BUFF 交易截圖。\n"
        "注意：只有**打勾(✔)且有黃色邊框**的項目才是要賣的。\n"
        "未選中的項目（灰色、無邊框）忽略。\n"
        "對每個選中的項目提取以下資訊：\n"
        "1. name：皮膚中文名稱（如 蝴蝶刀 | 狩獵網格，含★標記）\n"
        "2. wear：磨損度中文（崭新出厂 / 略有磨损 / 久经沙场 / 战痕累累 / 破损不堪）\n"
        "3. price：價格數字\n"
        "4. currency：TWD 或 RMB\n\n"
        "回傳 JSON 陣列：\n"
        '[{"name":"蝴蝶刀 (★) | 狩獵網格","wear":"久经沙场","price":3027,"currency":"RMB"}]\n'
        "無選中項目回傳 []"
    )

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": VISION_MODEL,
        "messages": [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "max_tokens": 500,
        "temperature": 0.1,
    }

    for attempt in range(1, retry + 2):
        try:
            resp = requests.post(API_URL, headers=headers, json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
            text = data["choices"][0]["message"]["content"]

            # 清理可能的多餘文字
            text = text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[-1]
                text = text.rsplit("```", 1)[0]

            result = json.loads(text)

            # 支援自訂提示詞回傳陣列的情況（FB 截圖分析）
            if isinstance(result, list):
                print(f"  [Vision] ✅ 截圖分析完成，{len(result)} 筆結果")
                return result

            if result.get("market_hash_name") == "UNKNOWN":
                print("  [Vision] ⚠️  無法辨識皮膚")
                return None

            print(f"  [Vision] ✅ {result.get('market_hash_name', '?')}")
            print(f"           價格 NT${result.get('seller_price', -1):,.0f}")
            if result.get("notes"):
                print(f"           備註: {result['notes']}")
            return result

        except json.JSONDecodeError:
            print(f"  [Vision] ⚠️  JSON 解析失敗 (第{attempt}次)")
        except requests.exceptions.RequestException as e:
            print(f"  [Vision] ⚠️  API 錯誤：{e} (第{attempt}次)")
        except Exception as e:
            print(f"  [Vision] ⚠️  未知錯誤：{e} (第{attempt}次)")

        if attempt <= retry:
            import time
            time.sleep(3)

    print("  [Vision] ❌ 已達最大重試次數")
    return None


def analyze_image_file(image_path: str) -> dict | None:
    """分析指定路徑的圖片檔案。

    Args:
        image_path: 圖片檔案路徑。

    Returns:
        同 analyze_image() 的 dict，或 None。
    """
    if not os.path.exists(image_path):
        print(f"  [Vision] ❌ 檔案不存在：{image_path}")
        return None

    with open(image_path, "rb") as f:
        image_bytes = f.read()

    return analyze_image(image_bytes)
