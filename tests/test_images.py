from __future__ import annotations

import base64

import pytest

from remote_agent_lite.images import ImageInputError, parse_image, parse_images


PNG_HEAD = b"\x89PNG\r\n\x1a\n"
JPEG_HEAD = b"\xff\xd8\xff"
GIF_HEAD = b"GIF89a"
WEBP_HEAD = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP"
BMP_HEAD = b"BM"


def data_url(mime: str, payload: bytes) -> str:
    return f"data:{mime};base64," + base64.b64encode(payload).decode()


@pytest.mark.parametrize(
    ("mime", "head", "name"),
    [
        ("image/png", PNG_HEAD, "shot.png"),
        ("image/jpeg", JPEG_HEAD, "photo.jpg"),
        ("image/gif", GIF_HEAD, "anim.gif"),
        ("image/webp", WEBP_HEAD, "pic.webp"),
        ("image/bmp", BMP_HEAD, "raw.bmp"),
    ],
)
def test_parse_image_accepts_supported_formats(mime, head, name) -> None:
    image = parse_image({"name": name, "data_url": data_url(mime, head + b"body")})
    assert image.mime == mime
    assert image.name == name
    assert image.size == len(head + b"body")
    assert image.data_url.startswith(f"data:{mime};base64,")


def test_parse_image_falls_back_to_a_default_name() -> None:
    image = parse_image({"data_url": data_url("image/png", PNG_HEAD)})
    assert image.name == "image.png"


@pytest.mark.parametrize(
    "raw",
    [
        {"data_url": "data:text/plain;base64,aGVsbG8="},
        {"data_url": "data:image/tiff;base64,aGVsbG8="},
        {"data_url": "data:image/png,notbase64"},
        {"data_url": "https://example.com/cat.png"},
        {"data_url": ""},
        {"data_url": data_url("image/png", b"not really a png")},
        {"data_url": data_url("image/jpeg", PNG_HEAD)},
    ],
)
def test_parse_image_rejects_junk(raw) -> None:
    with pytest.raises(ImageInputError):
        parse_image(raw)


def test_parse_images_has_no_count_or_size_cap() -> None:
    big = PNG_HEAD + b"x" * (4 * 1024 * 1024)
    items = [{"data_url": data_url("image/png", big)} for _ in range(24)]
    images = parse_images(items)
    assert len(images) == 24
    assert all(image.size == len(big) for image in images)
