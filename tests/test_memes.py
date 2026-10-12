import json
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from aling.services.memes import LocalMemeProvider, MemeCatalogError, parse_meme_command


def write_catalog(root, entries):
    root.mkdir(parents=True, exist_ok=True)
    (root / "manifest.json").write_text(json.dumps(entries), encoding="utf-8")


def image(path, *, size=(3, 3), format="PNG"):
    Image.new("RGB", size, "red").save(path, format)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("猫猫", ("local", "猫猫")),
        ("--mode local 猫猫", ("local", "猫猫")),
        ("--mode auto 猫猫", ("auto", "猫猫")),
        ("--mode online 猫猫", ("online", "猫猫")),
    ],
)
def test_meme_command_keeps_omitted_mode_local(value, expected):
    assert parse_meme_command(value) == expected


@pytest.mark.parametrize("value", ["--mode online", "--mode=online cat", "--mode unsafe cat"])
def test_meme_command_rejects_invalid_mode_syntax(value):
    with pytest.raises(MemeCatalogError):
        parse_meme_command(value)


async def test_local_meme_search_matches_tags_and_returns_one(tmp_path):
    image(tmp_path / "cat.png")
    write_catalog(
        tmp_path,
        [{"id": "cat-1", "description": "一脸无语", "tags": ["无语", "猫"], "path": "cat.png"}],
    )
    provider = LocalMemeProvider(tmp_path)
    assert [meme.description for meme in await provider.search("猫", limit=4)] == ["一脸无语"]
    assert await provider.search("找不到") == []
    assert provider.read_image("cat-1").mime_type == "image/png"


@pytest.mark.parametrize(
    "path",
    ["../outside.png", "/tmp/outside.png", "sub/../../outside.png"],
)
async def test_meme_path_traversal_is_rejected(tmp_path, path):
    write_catalog(
        tmp_path,
        [{"id": "x", "description": "x", "tags": ["x"], "path": path}],
    )
    provider = LocalMemeProvider(tmp_path)
    assert await provider.search("x") == []


async def test_meme_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside.png"
    image(outside)
    root = tmp_path / "memes"
    root.mkdir()
    (root / "escape.png").symlink_to(outside)
    write_catalog(
        root,
        [{"id": "escape", "description": "escape", "tags": ["escape"], "path": "escape.png"}],
    )
    provider = LocalMemeProvider(root)
    with pytest.raises(MemeCatalogError):
        provider.read_image("escape")


async def test_meme_manifest_corruption_and_spoofed_or_corrupt_images_fail(tmp_path):
    root = tmp_path / "memes"
    root.mkdir()
    (root / "manifest.json").write_text("not-json", encoding="utf-8")
    with pytest.raises(MemeCatalogError):
        await LocalMemeProvider(root).search("x")

    write_catalog(
        root,
        [{"id": "fake", "description": "fake", "tags": ["fake"], "path": "fake.png"}],
    )
    (root / "fake.png").write_text("not really png", encoding="utf-8")
    with pytest.raises(MemeCatalogError):
        LocalMemeProvider(root).read_image("fake")


async def test_meme_size_pixel_and_animation_limits(tmp_path):
    image(tmp_path / "large.png", size=(10, 10))
    first, second = Image.new("RGB", (3, 3), "red"), Image.new("RGB", (3, 3), "blue")
    first.save(tmp_path / "animated.gif", save_all=True, append_images=[second], duration=100)
    write_catalog(
        tmp_path,
        [
            {"id": "large", "description": "large", "tags": ["large"], "path": "large.png"},
            {
                "id": "animated",
                "description": "animated",
                "tags": ["animated"],
                "path": "animated.gif",
            },
        ],
    )
    provider = LocalMemeProvider(tmp_path, max_bytes=10, max_pixels=50)
    with pytest.raises(MemeCatalogError):
        provider.read_image("large")
    provider = LocalMemeProvider(tmp_path, max_pixels=50)
    with pytest.raises(MemeCatalogError):
        provider.read_image("large")
    with pytest.raises(MemeCatalogError):
        provider.read_image("animated")


async def test_missing_meme_directory_is_an_empty_catalog(tmp_path):
    assert await LocalMemeProvider(tmp_path / "missing").search("x") == []


def test_large_image_read_is_bounded_before_allocation(tmp_path, monkeypatch):
    asset = tmp_path / "large.png"
    asset.write_bytes(b"x" * 100)
    write_catalog(tmp_path, [{"id": "x", "description": "x", "tags": [], "path": asset.name}])
    original_open = Path.open

    class BoundedReader(BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 33, "Image reads must be bounded before allocation"
            return super().read(size)

    def guarded_open(path, *args, **kwargs):
        if path == asset:
            return BoundedReader(b"x" * 100)
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)
    with pytest.raises(MemeCatalogError, match="byte limit"):
        LocalMemeProvider(tmp_path, max_bytes=32).read_image("x")
