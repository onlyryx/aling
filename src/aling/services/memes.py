import asyncio
import hashlib
import json
import os
import re
import secrets
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from aling.core.extensions import MemeResult

MAX_MANIFEST_BYTES = 1_000_000
MAX_ENTRIES = 5000
MAX_MANAGED_BYTES = 200_000_000
RESOURCE = re.compile(r"[a-zA-Z0-9_-]{1,64}")
MANAGED = re.compile(r"upload-[a-f0-9]{64}\.(png|jpeg|webp|gif)")


class MemeCatalogError(ValueError):
    """The local catalog or a referenced image is invalid or unsafe."""


class MemeReadOnlyError(MemeCatalogError):
    pass


class MemeQuotaError(MemeCatalogError):
    pass


@dataclass(frozen=True)
class ValidatedImage:
    content: bytes
    mime_type: str


def validate_image_bytes(
    content: bytes, *, max_bytes: int = 5_000_000, max_pixels: int = 12_000_000
) -> ValidatedImage:
    if not content or len(content) > max_bytes:
        raise MemeCatalogError("Image exceeds the byte limit")
    try:
        with Image.open(BytesIO(content)) as image:
            image_format = image.format
            if image_format not in {"PNG", "JPEG", "WEBP", "GIF"}:
                raise MemeCatalogError("Unsupported image format")
            if image.width * image.height > max_pixels:
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
    return ValidatedImage(
        content,
        {"PNG": "image/png", "JPEG": "image/jpeg", "WEBP": "image/webp", "GIF": "image/gif"}[
            image_format
        ],
    )


def _metadata(description, tags, source="", license=""):
    if (
        not isinstance(description, str)
        or len(description) > 200
        or not isinstance(tags, list)
        or len(tags) > 32
        or any(not isinstance(tag, str) or not tag.strip() or len(tag) > 80 for tag in tags)
        or not isinstance(source, str)
        or len(source) > 500
        or not isinstance(license, str)
        or len(license) > 500
    ):
        raise MemeCatalogError("Invalid metadata")


class LocalMemeProvider:
    def __init__(
        self, root: Path, *, max_bytes: int = 5_000_000, max_pixels: int = 12_000_000
    ) -> None:
        self._write_root = root.expanduser().absolute()
        self.root = self._write_root.resolve()
        self.max_bytes = max_bytes
        self.max_pixels = max_pixels
        self._write_lock = asyncio.Lock()

    async def search(self, query: str, *, limit: int = 1) -> list[MemeResult]:
        if not isinstance(query, str) or not query.strip() or len(query) > 100:
            return []
        needle = query.casefold().strip()
        matches = []
        for entry in self._entries():
            text = " ".join([entry["description"], *entry["tags"]]).casefold()
            if (
                entry["description"].strip()
                and needle in text
                and self._safe_path(entry["path"]) is not None
            ):
                matches.append(MemeResult(entry["id"], entry["description"]))
        return matches[: max(0, min(limit, 1))]

    def read_image(self, resource_id: str) -> ValidatedImage:
        if not isinstance(resource_id, str) or not RESOURCE.fullmatch(resource_id):
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
        return validate_image_bytes(content, max_bytes=self.max_bytes, max_pixels=self.max_pixels)

    def _decode(self, content):
        if len(content) > MAX_MANIFEST_BYTES:
            raise MemeCatalogError("Manifest is too large")
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeError, ValueError, RecursionError) as exc:
            raise MemeCatalogError("Manifest is invalid") from exc
        if not isinstance(payload, list) or len(payload) > MAX_ENTRIES:
            raise MemeCatalogError("Manifest must be a bounded list")
        seen = set()
        for item in payload:
            if not isinstance(item, dict):
                raise MemeCatalogError("Manifest entry is invalid")
            resource = item.get("id")
            if (
                not isinstance(resource, str)
                or not RESOURCE.fullmatch(resource)
                or resource in seen
                or not isinstance(item.get("path"), str)
            ):
                raise MemeCatalogError("Manifest entry is invalid")
            _metadata(
                item.get("description"),
                item.get("tags"),
                item.get("source", ""),
                item.get("license", ""),
            )
            seen.add(resource)
        return payload

    def _entries(self) -> list[dict[str, object]]:
        manifest = self.root / "manifest.json"
        if not manifest.exists():
            return []
        if self._safe_path("manifest.json") is None:
            raise MemeCatalogError("Manifest path is unsafe")
        try:
            with manifest.open("rb") as stream:
                return self._decode(stream.read(MAX_MANIFEST_BYTES + 1))
        except OSError as exc:
            raise MemeCatalogError("Manifest cannot be read") from exc

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

    def _write_reason(self):
        try:
            if any(path.is_symlink() for path in [self._write_root, *self._write_root.parents]):
                return "unsafe_path"
            if not self._write_root.is_dir():
                return "missing_directory"
            for name in ("manifest.json", ".manifest.previous.json"):
                path = self._write_root / name
                if path.is_symlink() or (path.exists() and not path.is_file()):
                    return "unsafe_path"
                if path.exists() and (
                    not path.stat().st_mode & 0o222 or not os.access(path, os.W_OK)
                ):
                    return "permission_denied"
            if not self._write_root.stat().st_mode & 0o222 or not os.access(
                self._write_root, os.W_OK | os.X_OK
            ):
                return "permission_denied"
        except OSError:
            return "permission_denied"
        return ""

    @contextmanager
    def _directory(self):
        if self._write_reason():
            raise MemeReadOnlyError("Asset directory is read-only")
        try:
            fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
            try:
                for component in self._write_root.parts[1:]:
                    next_fd = os.open(
                        component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
                    )
                    os.close(fd)
                    fd = next_fd
                yield fd
            finally:
                os.close(fd)
        except OSError as exc:
            raise MemeCatalogError("Asset operation failed") from exc

    def _managed_bytes(self, fd):
        total = 0
        for name in os.listdir(fd):
            if MANAGED.fullmatch(name):
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise MemeCatalogError("Managed image path is unsafe")
                total += info.st_size
        return total

    def write_status(self) -> dict:
        reason = self._write_reason()
        used = None
        if not reason:
            try:
                with self._directory() as fd:
                    used = self._managed_bytes(fd)
                    self._load_write(fd)
            except MemeCatalogError:
                reason = "invalid_catalog"
        return {
            "writable": not reason,
            "reason": reason,
            "managed_bytes": used,
            "max_managed_bytes": MAX_MANAGED_BYTES,
            "upload_available": not reason and used < MAX_MANAGED_BYTES,
        }

    def list_assets(self, *, offset: int = 0, limit: int = 100, query: str = "") -> dict:
        needle = query[:100].strip().casefold()
        entries = [
            entry
            for entry in self._entries()
            if not needle
            or needle in " ".join([entry["id"], entry["description"], *entry["tags"]]).casefold()
        ]
        items = [
            {key: entry.get(key, "") for key in ("id", "description", "tags", "source", "license")}
            for entry in entries[max(0, offset) : max(0, offset) + max(0, min(limit, 100))]
        ]
        return {
            "items": items,
            "total": len(entries),
            "offset": max(0, offset),
            "write_status": self.write_status(),
        }

    def _load_write(self, fd):
        try:
            stream_fd = os.open("manifest.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
        except FileNotFoundError:
            return [], None
        with os.fdopen(stream_fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise MemeCatalogError("Manifest path is unsafe")
            content = stream.read(MAX_MANIFEST_BYTES + 1)
        return self._decode(content), content

    def _serialize(self, entries):
        try:
            content = json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8")
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise MemeCatalogError("Manifest is invalid") from exc
        self._decode(content)
        return content

    def _publish(self, fd, content, previous):
        temporary = []
        try:
            for destination, data in [
                (".manifest.previous.json", previous),
                ("manifest.json", content),
            ]:
                if data is None:
                    continue
                name = ".manifest-" + secrets.token_hex(12) + ".tmp"
                stream_fd = os.open(
                    name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=fd
                )
                temporary.append(name)
                with os.fdopen(stream_fd, "wb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                # Destination symlinks are replaced, never followed; reject them explicitly.
                try:
                    info = os.stat(destination, dir_fd=fd, follow_symlinks=False)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise MemeCatalogError("Manifest path is unsafe")
                except FileNotFoundError:
                    pass
                os.replace(name, destination, src_dir_fd=fd, dst_dir_fd=fd)
        finally:
            for name in temporary:
                try:
                    os.unlink(name, dir_fd=fd)
                except FileNotFoundError:
                    pass

    async def _write(self, operation):
        async with self._write_lock:
            # Keep the lock until the thread completes, including client cancellation.
            task = asyncio.create_task(asyncio.to_thread(operation))
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    cancelled = True
            if cancelled:
                raise asyncio.CancelledError
            return result

    async def add_image(self, content: bytes) -> str:
        def add():
            validated = validate_image_bytes(
                content, max_bytes=self.max_bytes, max_pixels=self.max_pixels
            )
            resource = hashlib.sha256(content).hexdigest()
            name = f"upload-{resource}.{validated.mime_type.split('/')[1]}"
            with self._directory() as fd:
                entries, previous = self._load_write(fd)
                existing_entry = next((entry for entry in entries if entry["id"] == resource), None)
                if existing_entry is not None and existing_entry["path"] != name:
                    raise MemeCatalogError("Managed image path is unsafe")
                if existing_entry is None:
                    entries.append(
                        dict(id=resource, description="", tags=[], path=name, source="", license="")
                    )
                serialized = self._serialize(entries)
                used = self._managed_bytes(fd)
                created = False
                try:
                    try:
                        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                            raise MemeCatalogError("Managed image path is unsafe")
                        existing_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=fd)
                        with os.fdopen(existing_fd, "rb") as stream:
                            if stream.read(self.max_bytes + 1) != content:
                                raise MemeCatalogError("Managed image is invalid")
                    except FileNotFoundError:
                        if used + len(content) > MAX_MANAGED_BYTES:
                            raise MemeQuotaError("Uploaded image quota exceeded") from None
                        image_fd = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                            0o600,
                            dir_fd=fd,
                        )
                        created = True
                        with os.fdopen(image_fd, "wb") as stream:
                            stream.write(content)
                            stream.flush()
                            os.fsync(stream.fileno())
                    if used > MAX_MANAGED_BYTES:
                        raise MemeQuotaError("Uploaded image quota exceeded")
                    if existing_entry is None:
                        self._publish(fd, serialized, previous)
                except BaseException:
                    if created:
                        os.unlink(name, dir_fd=fd)
                    raise
            return resource

        return await self._write(add)

    def _check_entry_path(self, path):
        candidate = Path(path)
        if candidate.is_absolute() or ".." in candidate.parts or self._safe_path(path) is None:
            raise MemeCatalogError("Image path is unsafe")
        target = self.root / candidate
        if any(part.is_symlink() for part in [target, *target.parents] if part != self.root.parent):
            raise MemeCatalogError("Image path is unsafe")

    async def update_metadata(
        self,
        resource_id: str,
        *,
        description: str,
        tags: list[str],
        source: str = "",
        license: str = "",
    ) -> None:
        _metadata(description, tags, source, license)

        def update():
            with self._directory() as fd:
                entries, previous = self._load_write(fd)
                entry = next((entry for entry in entries if entry["id"] == resource_id), None)
                if entry is None:
                    raise MemeCatalogError("Unknown image resource")
                self._check_entry_path(entry["path"])
                entry.update(description=description, tags=tags, source=source, license=license)
                self._publish(fd, self._serialize(entries), previous)

        await self._write(update)

    async def remove_entry(self, resource_id: str) -> bool:
        def remove():
            with self._directory() as fd:
                entries, previous = self._load_write(fd)
                remaining = [entry for entry in entries if entry["id"] != resource_id]
                if len(remaining) == len(entries):
                    return False
                self._publish(fd, self._serialize(remaining), previous)
                return True

        return await self._write(remove)
