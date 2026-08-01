"""領域列舉（V2 domain enums）。"""
from enum import Enum


class Currency(str, Enum):
    """支援的幣別。UNKNOWN 用於尚未判定的價格。"""
    TWD = "TWD"
    RMB = "RMB"
    USD = "USD"
    UNKNOWN = "UNKNOWN"
