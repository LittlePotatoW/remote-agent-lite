from __future__ import annotations

import pytest

from remote_agent_lite.utils import (
    is_text_file,
    safe_relative_path,
    sanitize_filename,
    slugify,
)


def test_safe_relative_path_rejects_traversal() -> None:
    assert safe_relative_path("uploads/a.txt") == "uploads/a.txt"
    assert safe_relative_path("") == ""
    with pytest.raises(ValueError):
        safe_relative_path("../secret")
    with pytest.raises(ValueError):
        safe_relative_path("/etc/passwd")


def test_filename_and_slug_are_safe() -> None:
    assert sanitize_filename("../../evil.txt") == "evil.txt"
    assert sanitize_filename("..") == "file"
    assert slugify("你好 world!") == "world"
    assert slugify("你好").startswith("project-")


def test_text_detection(tmp_path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("hello", encoding="utf-8")
    binary = tmp_path / "a.bin"
    binary.write_bytes(b"\x00\x01\x02")
    assert is_text_file(text)
    assert not is_text_file(binary)

