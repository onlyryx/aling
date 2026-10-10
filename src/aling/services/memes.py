import json
import re
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from aling.core.extensions import MemeResult


class MemeCatalogError(ValueError):
    """The local catalog or a referenced image is invalid or unsafe."""


@dataclass(frozen=True)
class ValidatedImage:
    content: bytes
    mime_type: str


class LocalMemeProvider:
    def __init__(
        self, root: Path, *, max_bytes: int = 5_000_000, max_pixels: int = 12_000_000
    ) -> None:
        self.root = root.expanduser().resolve()
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels

    async def search(self, query: str, *, limit: int = 1) -> list[MemeResult]:
        if not isinstance(query, str) or not query.strip() or len(query) > 100:
            return []
        entries = self._entries()
        needle = query.casefold().strip()
        matches: list[MemeResult] = []
        for entry in entries:
            text = " ".join([entry["description"], *entry["tags"]]).casefold()
            if needle in text and self._safe_path(entry["path"]) is not None:
                matches.append(MemeResult(entry["id"], entry["description"]))
        return matches[: max(0, min(limit, 1))]

    def read_image(self, resource_id: str) -> ValidatedImage:
        if not isinstance(resource_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9_-]{1,64}", resource_id
        ):
            raise MemeCatalogError("Unknown image resource")
        entry = next((entry for entry in self._entries() if entry["id"] == resource_id), None)
        if entry is None:
            raise MemeCatalogError("Unknown image resource")
        path = self._safe_path(entry["path"])
        if path is None:
            raise MemeCatalogError("Image path is outside the asset directory")
        try:
            with path.open("rb") as stream:
                content = stream.read(self.max_bytes + 1)
        except OSError as exc:
            raise MemeCatalogError("Image cannot be read") from exc
        if not content or len(content) > self.max_bytes:
            raise MemeCatalogError("Image exceeds the byte limit")
        try:
            with Image.open(BytesIO(content)) as image:
                image_format = image.format
                if image_format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                    raise MemeCatalogError("Unsupported image format")
                if image.width * image.height > self.max_pixels:
                    raise MemeCatalogError("Image exceeds the pixel limit")
                if getattr(image, "n_frames", 1) != 1:
                    raise MemeCatalogError("Animated images are not supported")
                image.verify()
            with Image.open(BytesIO(content)) as image:
                image.load()
        except MemeCatalogError:
            raise
        except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError) as exc:
            raise MemeCatalogError("Image data is invalid") from exc
        mime_type = {
            "PNG": "image/png",
            "JPEG": "image/jpeg",
            "WEBP": "image/webp",
            "GIF": "image/gif",
        }[image_format]
        return ValidatedImage(content, mime_type)

    def _entries(self) -> list[dict[str, object]]:
        manifest = self.root / "manifest.json"
        if not manifest.exists():
            return []
        if self._safe_path("manifest.json") is None:
            raise MemeCatalogError("Manifest path is unsafe")
        try:
            with manifest.open("rb") as stream:
                content = stream.read(1_000_001)
            if len(content) > 1_000_000:
                raise MemeCatalogError("Manifest is too large")
            payload = json.loads(content.decode("utf-8"))
        except MemeCatalogError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise MemeCatalogError("Manifest is invalid") from exc
        if not isinstance(payload, list) or len(payload) > 5000:
            raise MemeCatalogError("Manifest must be a bounded list")
        entries = []
        seen = set()
        for item in payload:
            if not isinstance(item, dict):
                raise MemeCatalogError("Manifest entry is invalid")
            resource_id, description, tags, path = (
                item.get("id"),
                item.get("description"),
                item.get("tags"),
                item.get("path"),
            )
            if (
                not isinstance(resource_id, str)
                or not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", resource_id)
                or resource_id in seen
                or not isinstance(description, str)
                or not description.strip()
                or len(description) > 200
                or not isinstance(tags, list)
                or len(tags) > 32
                or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in tags)
                or not isinstance(path, str)
            ):
                raise MemeCatalogError("Manifest entry is invalid")
            seen.add(resource_id)
            entries.append(
                {"id": resource_id, "description": description, "tags": tags, "path": path}
            )
        return entries

    def _safe_path(self, relative_path: str) -> Path | None:
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            return None
        candidate = Path(relative_path)
        if candidate.is_absolute():
            return None
        try:
            resolved = (self.root / candidate).resolve(strict=True)
            resolved.relative_to(self.root)
        except (OSError, ValueError):
            return None
        return resolved if resolved.is_file() else None
