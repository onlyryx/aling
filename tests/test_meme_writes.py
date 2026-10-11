import asyncio
import json
from io import BytesIO

import pytest
from PIL import Image

from aling.services.memes import LocalMemeProvider, MemeCatalogError


def picture(format="PNG", size=(3, 3), animated=False):
    buffer = BytesIO()
    image = Image.new("RGB", size, "red")
    image.save(
        buffer,
        format,
        save_all=animated,
        append_images=[Image.new("RGB", size, "blue")] if animated else [],
    )
    return buffer.getvalue()


def catalog(root):
    (root / "shared.png").write_bytes(picture())
    entries = [
        dict(id=key, description=key, tags=[], path="shared.png", custom={"keep": True})
        for key in ["entry-a", "entry-b"]
    ]
    (root / "manifest.json").write_text(json.dumps(entries))
    return LocalMemeProvider(root)


async def test_upload_metadata_search_remove_and_unknown_fields(tmp_path):
    provider = catalog(tmp_path)
    resource = await provider.add_image(picture())
    uploaded = next(item for item in provider.list_assets()["items"] if item["id"] == resource)
    assert uploaded["description"] == "" and uploaded["tags"] == []
    assert await provider.search("猫") == []
    await provider.update_metadata(
        resource, description="猫", tags=["无语"], source="s" * 500, license="l" * 500
    )
    assert (await provider.search("猫"))[0].resource_id == resource
    assert provider.read_image(resource).mime_type == "image/png"
    assert await provider.remove_entry("entry-a")
    assert (tmp_path / "shared.png").is_file()
    assert provider.read_image("entry-b").content
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    assert manifest[0]["custom"] == {"keep": True}
    assert manifest[-1]["source"] == "s" * 500
    assert len(list(tmp_path.glob(".manifest*.tmp"))) == 0
    assert (tmp_path / ".manifest.previous.json").is_file()


@pytest.mark.parametrize(
    "field,value",
    [
        ("description", "x" * 201),
        ("tags", ["x"] * 33),
        ("tags", ["x" * 81]),
        ("source", "x" * 501),
        ("license", "x" * 501),
    ],
)
async def test_metadata_limits_leave_manifest_unchanged(tmp_path, field, value):
    provider = catalog(tmp_path)
    before = (tmp_path / "manifest.json").read_bytes()
    metadata = dict(description="valid", tags=[], source="", license="")
    metadata[field] = value
    with pytest.raises(MemeCatalogError):
        await provider.update_metadata("entry-a", **metadata)
    assert (tmp_path / "manifest.json").read_bytes() == before


@pytest.mark.parametrize(
    "content",
    [b"x" * 5_000_001, picture("GIF", animated=True), picture(size=(4001, 3000))],
    ids=["bytes", "animation", "pixels"],
)
async def test_image_limits_do_not_publish(tmp_path, content):
    provider = catalog(tmp_path)
    before = (tmp_path / "manifest.json").read_bytes()
    with pytest.raises(MemeCatalogError):
        await provider.add_image(content)
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert not list(tmp_path.glob("upload-*"))


async def test_static_gif_and_custom_provider_limits(tmp_path):
    provider = LocalMemeProvider(tmp_path)
    resource = await provider.add_image(picture("GIF"))
    assert provider.read_image(resource).mime_type == "image/gif"
    with pytest.raises(MemeCatalogError):
        await LocalMemeProvider(tmp_path, max_bytes=10).add_image(picture())


@pytest.mark.parametrize(
    "bad",
    [
        b"not json",
        b" " * 1_000_001,
        json.dumps(
            [dict(id=str(i), description="x", tags=[], path="shared.png") for i in range(5001)]
        ).encode(),
    ],
    ids=["corrupt", "bytes", "entries"],
)
async def test_corrupt_or_oversized_manifest_never_replaced(tmp_path, bad):
    (tmp_path / "manifest.json").write_bytes(bad)
    with pytest.raises(MemeCatalogError):
        await LocalMemeProvider(tmp_path).add_image(picture())
    assert (tmp_path / "manifest.json").read_bytes() == bad
    assert not list(tmp_path.glob("upload-*"))


async def test_atomic_failure_cleans_only_new_file(tmp_path, monkeypatch):
    import os

    provider = catalog(tmp_path)
    before = (tmp_path / "manifest.json").read_bytes()
    replace = os.replace

    def fail_manifest(source, destination, *args, **kwargs):
        if destination == "manifest.json":
            raise OSError("fictional private error")
        return replace(source, destination, *args, **kwargs)

    monkeypatch.setattr(os, "replace", fail_manifest)
    with pytest.raises(MemeCatalogError):
        await provider.add_image(picture())
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert (tmp_path / "shared.png").is_file()
    assert not list(tmp_path.glob("upload-*"))
    assert not list(tmp_path.glob("*.tmp"))


async def test_symlink_and_traversal_writes_rejected(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    provider = catalog(root)
    outside = tmp_path / "outside.json"
    outside.write_text("[]")
    (root / "manifest.json").unlink()
    (root / "manifest.json").symlink_to(outside)
    assert not provider.write_status()["writable"]
    with pytest.raises(MemeCatalogError):
        await provider.add_image(picture())
    assert outside.read_text() == "[]"
    (root / "manifest.json").unlink()
    (root / "manifest.json").write_text(
        json.dumps([dict(id="unsafe", description="x", tags=[], path="../outside.json")])
    )
    with pytest.raises(MemeCatalogError):
        await provider.update_metadata("unsafe", description="changed", tags=[])
    link = tmp_path / "linked"
    link.symlink_to(root, target_is_directory=True)
    assert not LocalMemeProvider(link).write_status()["writable"]


async def test_managed_quota_counts_removed_assets(tmp_path):
    provider = LocalMemeProvider(tmp_path)
    resource = await provider.add_image(picture())
    await provider.remove_entry(resource)
    with (tmp_path / ("upload-" + "f" * 64 + ".png")).open("wb") as stream:
        stream.truncate(200_000_000)
    assert provider.write_status()["managed_bytes"] >= 200_000_000
    with pytest.raises(MemeCatalogError):
        await provider.add_image(picture("GIF"))


async def test_concurrent_metadata_updates_are_complete(tmp_path):
    provider = catalog(tmp_path)
    await asyncio.gather(
        provider.update_metadata("entry-a", description="A", tags=[]),
        provider.update_metadata("entry-b", description="B", tags=[]),
    )
    assert [
        item["description"] for item in json.loads((tmp_path / "manifest.json").read_text())
    ] == ["A", "B"]


def test_doctor_reports_asset_permission_without_creating_directory(tmp_path, monkeypatch, capsys):
    from types import SimpleNamespace

    from aling import cli

    root = tmp_path / "missing-assets"
    monkeypatch.setattr(
        cli,
        "Settings",
        lambda: SimpleNamespace(
            database_path=tmp_path / "bot.db", meme_dir=root, host="127.0.0.1", port=0
        ),
    )
    assert cli.doctor() == 0
    assert "素材目录：只读（目录不存在）" in capsys.readouterr().out
    assert not root.exists()
    root.mkdir()
    assert cli.doctor() == 0
    assert "素材目录：可写" in capsys.readouterr().out
    root.chmod(0o500)
    try:
        assert cli.doctor() == 0
        assert "素材目录：只读（权限或只读挂载）" in capsys.readouterr().out
    finally:
        root.chmod(0o700)


async def test_full_catalog_refuses_add_and_keeps_previous_manifest(tmp_path):
    payload = [dict(id=str(i), description="x", tags=[], path="existing.png") for i in range(5000)]
    before = json.dumps(payload).encode()
    (tmp_path / "manifest.json").write_bytes(before)
    with pytest.raises(MemeCatalogError):
        await LocalMemeProvider(tmp_path).add_image(picture())
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert not list(tmp_path.glob("upload-*"))


async def test_serialized_manifest_byte_limit_preserves_original(tmp_path):
    provider = catalog(tmp_path)
    entries = json.loads((tmp_path / "manifest.json").read_text())
    entries[0]["unknown"] = "x" * 999_750
    before = json.dumps(entries, separators=(",", ":")).encode()
    assert len(before) <= 1_000_000
    (tmp_path / "manifest.json").write_bytes(before)
    with pytest.raises(MemeCatalogError):
        await provider.update_metadata("entry-a", description="x" * 200, tags=[])
    assert (tmp_path / "manifest.json").read_bytes() == before


async def test_generated_file_and_backup_symlinks_are_rejected(tmp_path):
    import hashlib

    provider = catalog(tmp_path)
    outside = tmp_path.parent / (tmp_path.name + "-outside.png")
    outside.write_bytes(b"untouched")
    content = picture()
    target = tmp_path / f"upload-{hashlib.sha256(content).hexdigest()}.png"
    target.symlink_to(outside)
    with pytest.raises(MemeCatalogError):
        await provider.add_image(content)
    assert outside.read_bytes() == b"untouched"
    target.unlink()
    (tmp_path / ".manifest.previous.json").symlink_to(outside)
    with pytest.raises(MemeCatalogError):
        await provider.update_metadata("entry-a", description="x", tags=[])
    assert outside.read_bytes() == b"untouched"


@pytest.mark.parametrize(
    "format,mime",
    [("PNG", "image/png"), ("JPEG", "image/jpeg"), ("WEBP", "image/webp"), ("GIF", "image/gif")],
)
def test_shared_validator_supports_only_static_image_formats(format, mime):
    from aling.services.memes import validate_image_bytes

    assert validate_image_bytes(picture(format)).mime_type == mime
    with pytest.raises(MemeCatalogError):
        validate_image_bytes(picture(format), max_pixels=8)
    with pytest.raises(MemeCatalogError):
        validate_image_bytes(picture("BMP"))


async def test_managed_upload_reuse_does_not_overwrite_or_clear_metadata(tmp_path):
    provider = LocalMemeProvider(tmp_path)
    resource = await provider.add_image(picture())
    await provider.update_metadata(resource, description="already assigned", tags=["existing"])
    before = (tmp_path / "manifest.json").read_bytes()
    assert await provider.add_image(picture()) == resource
    assert (tmp_path / "manifest.json").read_bytes() == before
    assert provider.list_assets(query=resource)["items"][0]["description"] == "already assigned"


async def test_repeated_cancellation_keeps_writer_serialized(tmp_path, monkeypatch):
    import threading

    provider = catalog(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    original = provider._publish
    calls = []

    def paused_publish(*args):
        calls.append(True)
        if len(calls) == 1:
            entered.set()
            assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(provider, "_publish", paused_publish)
    first = asyncio.create_task(provider.update_metadata("entry-a", description="A", tags=[]))
    assert await asyncio.to_thread(entered.wait, 1)
    first.cancel()
    await asyncio.sleep(0)
    first.cancel()
    second = asyncio.create_task(provider.update_metadata("entry-b", description="B", tags=[]))
    await asyncio.sleep(0.05)
    try:
        assert len(calls) == 1, "cancelled thread must keep write lock until publication finishes"
    finally:
        release.set()
        await asyncio.gather(first, second, return_exceptions=True)
    assert [entry["description"] for entry in provider._entries()] == ["A", "B"]


async def test_duplicate_upload_rejects_changed_managed_symlink(tmp_path):
    provider = LocalMemeProvider(tmp_path)
    resource = await provider.add_image(picture())
    asset = tmp_path / provider._entries()[0]["path"]
    outside = tmp_path / "original.png"
    asset.rename(outside)
    asset.symlink_to(outside)
    with pytest.raises(MemeCatalogError):
        await provider.add_image(picture())
    assert provider._entries()[0]["id"] == resource


def test_exact_image_byte_and_pixel_boundaries():
    from aling.services.memes import validate_image_bytes

    content = picture()
    padded = content + b"\0" * (5_000_000 - len(content))
    assert len(validate_image_bytes(padded).content) == 5_000_000
    with pytest.raises(MemeCatalogError):
        validate_image_bytes(padded + b"\0")
    assert validate_image_bytes(picture(size=(4000, 3000))).mime_type == "image/png"
    with pytest.raises(MemeCatalogError):
        validate_image_bytes(picture(size=(12_000_001, 1)))
