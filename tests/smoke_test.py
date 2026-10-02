"""Self-check for the PDF pipeline.

Creates a handful of synthetic "passport" images in a temp folder, builds a
PDF from them in a known order, and verifies page count, page order (via image
dimensions) and geometry. Run with:

    python tests/smoke_test.py
"""

from __future__ import annotations

import io
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402
from pypdf import PdfReader  # noqa: E402

from app.images import list_images  # noqa: E402
from app.pdfbuild import PageItem, PageSettings, build_pdf  # noqa: E402

PASSED = 0
FAILED: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASSED
    if condition:
        PASSED += 1
        print(f"  PASS  {label}")
    else:
        FAILED.append(label)
        print(f"  FAIL  {label} {detail}")


def make_images(folder: Path, specs: list[tuple[str, tuple[int, int], tuple[int, int, int]]]) -> None:
    for name, size, color in specs:
        img = Image.new("RGB", size, color)
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(0, 0, 0), width=6)
        draw.text((20, 20), name, fill=(0, 0, 0))
        img.save(folder / name)


def page_pixel_size(reader: PdfReader, index: int) -> tuple[float, float]:
    box = reader.pages[index].mediabox
    return (float(box.width), float(box.height))


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        make_images(
            folder,
            [
                ("passport-1.png", (600, 900), (230, 230, 255)),   # portrait
                ("passport-2.png", (900, 600), (255, 230, 230)),   # landscape
                ("passport-10.png", (600, 800), (230, 255, 230)),
            ],
        )
        # A non-image file must be ignored by the scanner.
        (folder / "notes.txt").write_text("ignore me", encoding="utf-8")

        print("\nimage discovery")
        images = list_images(str(folder), recursive=True)
        names = [item["name"] for item in images]
        check("finds exactly the 3 images", len(images) == 3, str(names))
        check(
            "sorts 2 before 10 (natural order)",
            names == ["passport-1.png", "passport-2.png", "passport-10.png"],
            str(names),
        )

        print("\nA4 portrait, fit inside page")
        items = [PageItem(path=item["path"], name=item["name"]) for item in images]
        settings = PageSettings(page_size="a4", orientation="portrait", fit="contain", margin_mm=0)
        data = build_pdf(items, settings)
        check("produces a non-trivial PDF", len(data) > 2000, f"{len(data)} bytes")
        check("PDF magic header", data[:5] == b"%PDF-", data[:8])

        reader = PdfReader(io.BytesIO(data))
        check("one page per image", len(reader.pages) == 3, str(len(reader.pages)))
        for index in range(len(reader.pages)):
            width, height = page_pixel_size(reader, index)
            check(
                f"page {index + 1} is A4 portrait",
                abs(width - 595.276) < 1 and abs(height - 841.890) < 1,
                f"{width:.1f}x{height:.1f}",
            )

        print("\norder is preserved")
        # Each generated image has a distinct dominant colour; sample the middle
        # of each rendered page to confirm the order survived the build.
        small = build_pdf(items, PageSettings(page_size="a4", orientation="portrait", fit="stretch"))
        reader_small = PdfReader(io.BytesIO(small))
        pixels = []
        for page in reader_small.pages:
            for image in page.images:
                img = Image.open(io.BytesIO(image.data)).convert("RGB")
                pixels.append(img.getpixel((img.width // 2, img.height // 2)))
        check("3 page images embedded", len(pixels) == 3, str(len(pixels)))
        check("page 1 is the bluish portrait", pixels[0][2] > pixels[0][0], str(pixels[0]))
        check("page 2 is the reddish landscape", pixels[1][0] > pixels[1][2], str(pixels[1]))

        print("\nbrowser sort (reverse name) changes page order")
        reversed_items = list(reversed(items))
        reversed_pdf = build_pdf(
            reversed_items, PageSettings(page_size="a4", orientation="portrait", fit="stretch")
        )
        reader_rev = PdfReader(io.BytesIO(reversed_pdf))
        first = None
        for image in reader_rev.pages[0].images:
            img = Image.open(io.BytesIO(image.data)).convert("RGB")
            first = img.getpixel((img.width // 2, img.height // 2))
        check("reversed order puts the green page first", first[1] > first[0] and first[1] > first[2], str(first))

        print("\nrotation")
        rotated = build_pdf(
            [PageItem(path=items[0].path, rotation=90)],
            PageSettings(page_size="auto", orientation="auto", fit="contain"),
        )
        box = PdfReader(io.BytesIO(rotated)).pages[0].mediabox
        check(
            "rotating a portrait image 90 deg yields a landscape page",
            float(box.width) > float(box.height),
            f"{float(box.width):.1f}x{float(box.height):.1f}",
        )

        print("\npage sizes")
        for size_key, expected in (("letter", (612.0, 792.0)), ("a5", (419.528, 595.276))):
            pdf = build_pdf(items[:1], PageSettings(page_size=size_key, orientation="portrait"))
            box = PdfReader(io.BytesIO(pdf)).pages[0].mediabox
            check(
                f"{size_key.upper()} geometry",
                abs(float(box.width) - expected[0]) < 1 and abs(float(box.height) - expected[1]) < 1,
                f"{float(box.width):.1f}x{float(box.height):.1f}",
            )

        print("\nmargins shrink the drawn area but not the page")
        pdf_margin = build_pdf(items[:1], PageSettings(page_size="a4", orientation="portrait", margin_mm=20))
        box = PdfReader(io.BytesIO(pdf_margin)).pages[0].mediabox
        check(
            "page stays A4 with a 20 mm margin",
            abs(float(box.width) - 595.276) < 1,
            f"{float(box.width):.1f}",
        )

    print(f"\n{PASSED} check(s) passed, {len(FAILED)} failed.")
    if FAILED:
        for label in FAILED:
            print(f"  - {label}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
