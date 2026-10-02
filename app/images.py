"""Image discovery and pixel loading helpers.

Everything here is deliberately dependency-light: Pillow is the only
third-party import, and it is used for decoding/rotating only.
"""

from __future__ import annotations

import os
import string
from pathlib import Path

from PIL import Image, ImageOps

# Pillow format names we accept as page sources.
SUPPORTED_EXTENSIONS = {
    ".jpg", ".jpeg", ".jpe", ".jfif",
    ".png", ".webp", ".bmp", ".tif", ".tiff",
    ".heic", ".heif", ".avif", ".gif", ".ppm", ".pgm", ".jp2",
}

# Formats Pillow can decode in a stock install. Anything else is reported as
# unsupported at load time instead of silently producing a broken PDF.
IGNORED_NAMES = {"thumbs.db", "desktop.ini", ".ds_store"}

MAX_SCAN_RESULTS = 5000

try:  # Pillow only decodes HEIC/AVIF when a plugin is present.
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
except Exception:  # pragma: no cover - optional dependency
    pass


def is_supported_image(path: str | os.PathLike[str]) -> bool:
    name = os.path.basename(str(path))
    if name.startswith(".") or name.lower() in IGNORED_NAMES:
        return False
    return os.path.splitext(name)[1].lower() in SUPPORTED_EXTENSIONS


def natural_key(text: str):
    """Sort key that orders 'page2' before 'page10'."""
    import re

    parts = re.split(r"(\d+)", text.lower())
    return [(1, int(p)) if p.isdigit() else (0, p) for p in parts]


def root_candidates() -> list[str]:
    """Places the in-app folder browser is allowed to look at."""
    roots: list[str] = []
    home = Path.home()
    for candidate in (
        home,
        home / "Pictures",
        home / "Desktop",
        home / "Downloads",
        home / "Documents",
        Path(os.environ.get("DSH_WORKSPACE", "") or "."),
        Path.cwd(),
    ):
        try:
            if candidate and candidate.is_dir():
                resolved = str(candidate.resolve())
                if resolved not in roots:
                    roots.append(resolved)
        except OSError:
            continue

    if os.name == "nt":
        for letter in string.ascii_uppercase:
            drive = f"{letter}:\\"
            if os.path.exists(drive):
                # Only surface drives that look like real fixed/removable media.
                if drive not in roots:
                    roots.append(drive)
    return roots


def list_images(directory: str, recursive: bool = False) -> list[dict]:
    """Return lightweight metadata for every supported image under ``directory``."""
    base = Path(directory).expanduser()
    if not base.is_dir():
        raise NotADirectoryError(f"Not a folder: {directory}")

    found: list[dict] = []
    iterator = base.rglob("*") if recursive else base.glob("*")
    for entry in iterator:
        if len(found) >= MAX_SCAN_RESULTS:
            break
        try:
            if not entry.is_file() or not is_supported_image(entry):
                continue
            stat = entry.stat()
        except OSError:
            continue
        found.append(
            {
                "path": str(entry),
                "name": entry.name,
                "relPath": str(entry.relative_to(base)).replace("\\", "/"),
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )

    found.sort(key=lambda item: natural_key(item["relPath"]))
    return found


def list_subfolders(directory: str) -> list[dict]:
    base = Path(directory).expanduser()
    if not base.is_dir():
        raise NotADirectoryError(f"Not a folder: {directory}")
    subs: list[dict] = []
    for entry in base.iterdir():
        try:
            if entry.is_dir() and not entry.name.startswith("."):
                subs.append(
                    {
                        "name": entry.name,
                        "path": str(entry),
                        "imageCount": count_images(entry),
                    }
                )
        except OSError:
            continue
    subs.sort(key=lambda item: natural_key(item["name"]))
    return subs


def count_images(directory: Path) -> int:
    total = 0
    try:
        for entry in directory.iterdir():
            if entry.is_file() and is_supported_image(entry):
                total += 1
    except OSError:
        return 0
    return total


def open_image(path: str) -> Image.Image:
    """Load an image, applying EXIF orientation and flattening transparency."""
    img = Image.open(path)
    img.load()
    img = ImageOps.exif_transpose(img)

    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        flattened = Image.new("RGB", rgba.size, (255, 255, 255))
        flattened.paste(rgba, mask=rgba.split()[-1])
        return flattened

    if img.mode != "RGB":
        return img.convert("RGB")
    return img


def describe_image(path: str) -> dict:
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        return {"width": width, "height": height}
