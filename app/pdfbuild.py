"""Assemble a single PDF from an ordered list of images.

One image becomes one page. Page size, fit mode, margin, background and JPEG
quality are all caller-controlled so the UI can expose them directly.
"""

from __future__ import annotations

import io
import json
import os
import tempfile
from dataclasses import dataclass, field

from PIL import Image
from pypdf import PdfReader, PdfWriter

from .images import open_image

# Page geometries in points (1/72 inch).
PAGE_SIZES: dict[str, tuple[float, float] | None] = {
    "a4": (595.276, 841.890),
    "a4-landscape": (841.890, 595.276),
    "letter": (612.0, 792.0),
    "letter-landscape": (792.0, 612.0),
    "a5": (419.528, 595.276),
    "a3": (841.890, 1190.551),
    "auto": None,  # each page matches its own image size
}

FIT_MODES = {"contain", "cover", "stretch", "actual"}

MAX_EDGE_PIXELS = 6000  # downsample anything larger, keeps output sane
RENDER_DPI = 96.0  # raster density used when compositing pages


@dataclass
class PageSettings:
    page_size: str = "a4"
    orientation: str = "auto"  # auto | portrait | landscape
    fit: str = "contain"
    margin_mm: float = 0.0
    quality: int = 92
    background: str = "#ffffff"

    @classmethod
    def from_payload(cls, payload: dict | None) -> "PageSettings":
        payload = payload or {}
        settings = cls()
        size = str(payload.get("pageSize", settings.page_size)).lower()
        settings.page_size = size if size in PAGE_SIZES else settings.page_size
        orientation = str(payload.get("orientation", settings.orientation)).lower()
        settings.orientation = orientation if orientation in {"auto", "portrait", "landscape"} else "auto"
        fit = str(payload.get("fit", settings.fit)).lower()
        settings.fit = fit if fit in FIT_MODES else settings.fit
        try:
            settings.margin_mm = max(0.0, min(60.0, float(payload.get("marginMm", 0) or 0)))
        except (TypeError, ValueError):
            settings.margin_mm = 0.0
        try:
            settings.quality = max(50, min(100, int(payload.get("quality", settings.quality))))
        except (TypeError, ValueError):
            settings.quality = 92
        settings.background = normalize_color(payload.get("background")) or "#ffffff"
        return settings


@dataclass
class PageItem:
    path: str
    rotation: int = 0
    name: str = ""
    extra: dict = field(default_factory=dict)


def normalize_color(value) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("#") and len(text) in (4, 7):
            try:
                int(text[1:], 16)
                return text
            except ValueError:
                return None
    return None


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    text = value.lstrip("#")
    if len(text) == 3:
        text = "".join(ch * 2 for ch in text)
    return int(text[0:2], 16), int(text[2:4], 16), int(text[4:6], 16)


def _target_size(settings: PageSettings, img_w: int, img_h: int) -> tuple[float, float]:
    """Page size in points for one image."""
    base = PAGE_SIZES.get(settings.page_size)
    if base is None:
        # 'auto': the page matches the image's physical size at RENDER_DPI.
        return (img_w * 72.0 / RENDER_DPI, img_h * 72.0 / RENDER_DPI)

    width, height = base
    orientation = settings.orientation
    if orientation == "portrait" and width > height:
        width, height = height, width
    elif orientation == "landscape" and height > width:
        width, height = height, width
    elif orientation == "auto":
        if (img_w > img_h) != (width > height):
            width, height = height, width
    return width, height


def _scale_image(img, box_w: float, box_h: float, fit: str):
    """Scale for rasterising at RENDER_DPI, returning (image, drawn point size)."""
    px_w, px_h = img.size
    point_w = px_w * 72.0 / RENDER_DPI
    point_h = px_h * 72.0 / RENDER_DPI

    if fit == "stretch":
        target_w = max(1, int(round(box_w * RENDER_DPI / 72.0)))
        target_h = max(1, int(round(box_h * RENDER_DPI / 72.0)))
        return img.resize((target_w, target_h), _resample()), (box_w, box_h)

    if fit == "actual":
        scale = 1.0
    elif fit == "cover":
        scale = max(box_w / point_w, box_h / point_h)
    else:  # contain
        scale = min(box_w / point_w, box_h / point_h)

    point_w *= scale
    point_h *= scale
    # The canvas is rasterised at RENDER_DPI and saved with dpi=(96,96), so one
    # canvas pixel is exactly 0.75 pt; resize to the pixel count that matches
    # the intended physical size.
    target_w = max(1, int(round(point_w * RENDER_DPI / 72.0)))
    target_h = max(1, int(round(point_h * RENDER_DPI / 72.0)))

    largest = max(target_w, target_h)
    if largest > MAX_EDGE_PIXELS:
        shrink = MAX_EDGE_PIXELS / largest
        target_w = max(1, int(round(target_w * shrink)))
        target_h = max(1, int(round(target_h * shrink)))
        point_w *= shrink
        point_h *= shrink

    if (target_w, target_h) != (px_w, px_h):
        img = img.resize((target_w, target_h), _resample())
    return img, (point_w, point_h)


def _resample():
    try:
        return Image.Resampling.LANCZOS
    except AttributeError:  # pragma: no cover - very old Pillow
        return Image.LANCZOS


def render_page(item: PageItem, settings: PageSettings) -> str:
    """Render one image to a single-page PDF and return its temp path."""
    img = open_image(item.path)

    rotation = int(item.rotation or 0) % 360
    if rotation:
        # Pillow rotates counter-clockwise; the UI rotates clockwise.
        img = img.rotate(-rotation, expand=True)

    page_w, page_h = _target_size(settings, *img.size)
    margin = settings.margin_mm * 72.0 / 25.4
    box_w = max(1.0, page_w - 2 * margin)
    box_h = max(1.0, page_h - 2 * margin)

    img, (draw_w, draw_h) = _scale_image(img, box_w, box_h, settings.fit)

    canvas = (
        max(1, int(round(page_w * RENDER_DPI / 72.0))),
        max(1, int(round(page_h * RENDER_DPI / 72.0))),
    )
    page = Image.new("RGB", canvas, _hex_to_rgb(settings.background))
    offset = ((page_w - draw_w) / 2.0, (page_h - draw_h) / 2.0)
    paste_at = (
        int(round(offset[0] * RENDER_DPI / 72.0)),
        int(round(offset[1] * RENDER_DPI / 72.0)),
    )
    page.paste(img, paste_at)

    handle = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False)
    handle.close()
    # `dpi` must be explicit: Pillow derives the physical page box from it
    # (ignoring the older `resolution` argument), and the default would shrink
    # every page to 75% of the requested size.
    page.save(handle.name, "PDF", dpi=(RENDER_DPI, RENDER_DPI), quality=settings.quality)
    page.close()
    return handle.name


def build_pdf(items: list[PageItem], settings: PageSettings) -> bytes:
    if not items:
        raise ValueError("No images were provided.")

    writer = PdfWriter()
    temp_files: list[str] = []
    try:
        for index, item in enumerate(items, start=1):
            if not os.path.isfile(item.path):
                raise FileNotFoundError(f"Image not found: {item.path}")
            page_pdf = render_page(item, settings)
            temp_files.append(page_pdf)
            writer.add_page(PdfReader(page_pdf).pages[0])

        writer.add_metadata(
            {
                "/Title": items[0].name or "Scanned pages",
                "/Creator": "Image to PDF Converter",
                "/Producer": "Image to PDF Converter (Pillow + pypdf)",
            }
        )
        buffer = io.BytesIO()
        writer.write(buffer)
        return buffer.getvalue()
    finally:
        writer.close()
        for path in temp_files:
            try:
                os.unlink(path)
            except OSError:
                pass


def parse_items(raw: str | list) -> list[PageItem]:
    payload = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(payload, list):
        raise ValueError("'items' must be a list of {path, rotation} objects.")
    items: list[PageItem] = []
    for entry in payload:
        if isinstance(entry, str):
            items.append(PageItem(path=entry))
            continue
        if not isinstance(entry, dict):
            raise ValueError("Each item must be an object with a 'path'.")
        path = entry.get("path")
        if not path:
            raise ValueError("Each item needs a 'path'.")
        try:
            rotation = int(entry.get("rotation", 0) or 0)
        except (TypeError, ValueError):
            rotation = 0
        items.append(
            PageItem(
                path=str(path),
                rotation=rotation,
                name=str(entry.get("name") or os.path.basename(str(path))),
            )
        )
    return items
