from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from typing import Any, Iterable

from .storage import IMAGE_MEDIA_TYPES


#: Media types accepted for inline chat images (same set the file preview supports).
ALLOWED_MEDIA_TYPES = frozenset(IMAGE_MEDIA_TYPES.values())

_DATA_URL_RE = re.compile(r"^data:(?P<mime>image/[a-z0-9.+-]+);base64,(?P<payload>[A-Za-z0-9+/=]+)$")

_DEFAULT_NAMES = {
    "image/png": "image.png",
    "image/jpeg": "image.jpg",
    "image/gif": "image.gif",
    "image/webp": "image.webp",
    "image/bmp": "image.bmp",
}


class ImageInputError(ValueError):
    """Raised when an inline chat image cannot be used."""


@dataclass(frozen=True)
class ChatImage:
    """A one-shot image that travels with a single turn and is never stored."""

    name: str
    mime: str
    data_url: str
    size: int


def _check_png(head: bytes) -> bool:
    return head.startswith(b"\x89PNG\r\n\x1a\n")


def _check_jpeg(head: bytes) -> bool:
    return head.startswith(b"\xff\xd8\xff")


def _check_gif(head: bytes) -> bool:
    return head.startswith((b"GIF87a", b"GIF89a"))


def _check_webp(head: bytes) -> bool:
    return head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _check_bmp(head: bytes) -> bool:
    return head.startswith(b"BM")


_MAGIC_CHECKS = {
    "image/png": _check_png,
    "image/jpeg": _check_jpeg,
    "image/gif": _check_gif,
    "image/webp": _check_webp,
    "image/bmp": _check_bmp,
}


def parse_image(raw: Any) -> ChatImage:
    """Validate one inline image payload and normalise it for the codex turn."""

    if not isinstance(raw, dict):
        raise ImageInputError("图片数据格式不正确")
    data_url = str(raw.get("data_url") or "").strip()
    match = _DATA_URL_RE.match(data_url)
    if not match:
        raise ImageInputError("只接受 png / jpg / gif / webp / bmp 的 base64 图片数据")
    mime = match.group("mime").lower()
    if mime not in ALLOWED_MEDIA_TYPES:
        raise ImageInputError("只接受 png / jpg / gif / webp / bmp 的 base64 图片数据")
    payload = match.group("payload")
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ImageInputError("图片数据不是合法的 base64") from exc
    if not decoded or not _MAGIC_CHECKS[mime](decoded):
        raise ImageInputError("图片内容与声明的格式不符")
    name = str(raw.get("name") or "").strip()[:240] or _DEFAULT_NAMES[mime]
    return ChatImage(
        name=name,
        mime=mime,
        data_url=f"data:{mime};base64,{payload}",
        size=len(decoded),
    )


def parse_images(raw_items: Iterable[Any]) -> list[ChatImage]:
    """Validate every inline image of a turn. There is no count or size cap."""

    return [parse_image(item) for item in raw_items]
