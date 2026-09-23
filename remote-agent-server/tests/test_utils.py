from __future__ import annotations

import pytest

from remote_agent_lite.utils import safe_relative_path, sanitize_filename, slugify


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
