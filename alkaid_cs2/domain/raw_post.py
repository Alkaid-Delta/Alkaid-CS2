"""
raw_post.py — RawPostInput（V2 Phase 5）

parse_post 的輸入介面，保持不可變語意（呼叫端不可污染）。
"""
from dataclasses import dataclass, field


@dataclass
class RawPostInput:
    post_id: str
    author: str = ""
    link: str = ""
    raw_text: str = ""
    image_urls: list[str] = field(default_factory=list)
    source: str = "facebook"
    metadata: dict[str, object] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.post_id, str) or not self.post_id.strip():
            raise ValueError("post_id 不可空白")
        if not isinstance(self.author, str):
            raise TypeError(f"author 必須是 str，收到 {type(self.author).__name__}")
        if not isinstance(self.link, str):
            raise TypeError(f"link 必須是 str，收到 {type(self.link).__name__}")
        if not isinstance(self.raw_text, str):
            raise TypeError(f"raw_text 必須是 str，收到 {type(self.raw_text).__name__}")
        # image_urls：必須 list[str]、不得含空白字串、不接受 None
        if self.image_urls is None or not isinstance(self.image_urls, list):
            raise TypeError("image_urls 必須是 list[str]（不接受 None）")
        if any(not isinstance(u, str) or not u.strip() for u in self.image_urls):
            raise ValueError("image_urls 不得含空白字串")
        if not isinstance(self.source, str) or not self.source.strip():
            raise ValueError("source 不可空白")
        if self.metadata is None or not isinstance(self.metadata, dict):
            raise TypeError("metadata 必須是 dict（不接受 None）")
