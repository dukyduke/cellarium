"""Wine images: uploads and optional AI generation through an OpenAI-compatible API."""

from __future__ import annotations

import base64
import secrets
from pathlib import Path

import httpx

from .config import Settings

ALLOWED = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"RIFF": "webp",
    b"GIF8": "gif",
}
MAX_BYTES = 10 * 1024 * 1024


class ImageError(Exception):
    pass


def sniff_extension(data: bytes) -> str:
    for magic, ext in ALLOWED.items():
        if data.startswith(magic):
            if ext == "webp" and data[8:12] != b"WEBP":
                continue
            return ext
    raise ImageError("unsupported image format (jpeg, png, webp, gif)")


def save_image(settings: Settings, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise ImageError("image larger than 10 MB")
    ext = sniff_extension(data)
    name = f"{secrets.token_hex(12)}.{ext}"
    (settings.images_dir / name).write_bytes(data)
    return name


def delete_image(settings: Settings, name: str | None) -> None:
    if not name:
        return
    path = image_path(settings, name)
    if path and path.exists():
        path.unlink()


def image_path(settings: Settings, name: str) -> Path | None:
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    path = settings.images_dir / name
    return path if path.is_file() else None


def build_prompt(wine: dict, type_label: str) -> str:
    """Neutral illustration: we must not fake a real label, so no brand text is requested."""
    bits = [f"a bottle of {type_label.lower()} wine"]
    if wine.get("appellation"):
        bits.append(f"from {wine['appellation']}")
    if wine.get("region"):
        bits.append(f"({wine['region']})")
    return (
        "Studio product photograph of " + " ".join(bits) + ", standing upright on a light neutral background, "
        "soft natural light, elegant minimalist label with abstract vineyard illustration and no readable text, "
        "no logos, realistic glass reflections, centered, portrait framing."
    )


async def generate_image(settings: Settings, prompt: str, client: httpx.AsyncClient | None = None) -> bytes:
    if not settings.image_generation_enabled:
        raise ImageError("image generation is not configured (CELLARIUM_IMAGE_API_KEY)")
    payload = {"model": settings.image_model, "prompt": prompt, "n": 1, "size": settings.image_size}
    headers = {"Authorization": f"Bearer {settings.image_api_key}"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=180)
    try:
        resp = await client.post(f"{settings.image_api_base}/images/generations", json=payload, headers=headers)
        if resp.status_code >= 400:
            raise ImageError(f"image API error {resp.status_code}: {resp.text[:300]}")
        item = (resp.json().get("data") or [{}])[0]
        if item.get("b64_json"):
            return base64.b64decode(item["b64_json"])
        if item.get("url"):
            img = await client.get(item["url"])
            img.raise_for_status()
            return img.content
        raise ImageError("image API returned no image")
    finally:
        if own:
            await client.aclose()
