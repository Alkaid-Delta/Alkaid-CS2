# -*- coding: utf-8 -*-
"""test_secure_image_loader.py — Secure loader（Phase 6.4C2-B0）"""
import hashlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))

import pytest  # noqa: E402

from alkaid_cs2.evaluation.secure_image_loader import (  # noqa: E402
    InMemorySecureImageLoader,
    SecureImageLoadError,
)


def _loader_with(img: bytes) -> InMemorySecureImageLoader:
    return InMemorySecureImageLoader(
        {"secure-store://img-1": img})


def test_invalid_secure_reference_rejected():
    loader = InMemorySecureImageLoader()
    with pytest.raises(SecureImageLoadError, match="secure_reference_invalid"):
        loader.load("plain-id", "a" * 64)


def test_http_reference_rejected():
    loader = InMemorySecureImageLoader()
    with pytest.raises(SecureImageLoadError, match="secure_reference_invalid"):
        loader.load("https://example.com/x.jpg", "a" * 64)


def test_local_path_reference_rejected():
    loader = InMemorySecureImageLoader()
    with pytest.raises(SecureImageLoadError, match="secure_reference_invalid"):
        loader.load(r"C:\Users\user\pic.jpg", "a" * 64)


def test_image_hash_mismatch_rejected():
    img = b"\x89PNG fake bytes"
    loader = _loader_with(img)
    with pytest.raises(SecureImageLoadError,
                       match="secure_image_hash_mismatch"):
        loader.load("secure-store://img-1", "f" * 64)


def test_valid_in_memory_image_loaded():
    img = b"\x89PNG fake bytes"
    loader = _loader_with(img)
    got = loader.load("secure-store://img-1",
                      hashlib.sha256(img).hexdigest())
    assert got == img


def test_image_bytes_not_written_to_disk(tmp_path, monkeypatch):
    import pathlib
    img = b"secret-image-bytes"
    loader = _loader_with(img)
    writes = []

    def _track(self, *a, **k):
        writes.append(a)
        return pathlib.Path.write_text.__wrapped__(self, *a, **k) \
            if hasattr(pathlib.Path.write_text, "__wrapped__") \
            else pathlib.Path.write_text(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "write_text", _track)
    got = loader.load("secure-store://img-1",
                      hashlib.sha256(img).hexdigest())
    assert got == img
    assert writes == [], "loader 不得寫任何檔案"


def test_image_bytes_not_logged(tmp_path, monkeypatch, capsys):
    import logging
    img = b"secret-image-bytes"
    loader = _loader_with(img)
    got = loader.load("secure-store://img-1",
                      hashlib.sha256(img).hexdigest())
    out = capsys.readouterr()
    assert "secret-image-bytes" not in out.out + out.err
    assert got == img
