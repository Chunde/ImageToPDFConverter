"""End-to-end test of the running HTTP API.

Assumes the app is already serving on the given base URL. Creates fixture
images, then exercises health, listing, thumbnails, both build paths and
clear error handling.
"""

from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # noqa: E402
from pypdf import PdfReader  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
FIXTURES = ROOT / "testdata" / "passports"

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


def get(path: str):
    with urllib.request.urlopen(BASE + path, timeout=120) as response:
        return response.status, response.headers, response.read()


def post_json(path: str, payload: dict):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.status, response.headers, response.read()


def post_multipart(path: str, fields: dict[str, str], files: list[tuple[str, str, bytes]]):
    boundary = "----smoke" + uuid.uuid4().hex
    body = io.BytesIO()
    for key, value in fields.items():
        body.write(f"--{boundary}\r\n".encode())
        body.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.write(value.encode("utf-8"))
        body.write(b"\r\n")
    for field_name, filename, data in files:
        body.write(f"--{boundary}\r\n".encode())
        body.write(
            f'Content-Disposition: form-data; name="{field_name}"; filename="{filename}"\r\n'.encode()
        )
        body.write(b"Content-Type: application/octet-stream\r\n\r\n")
        body.write(data)
        body.write(b"\r\n")
    body.write(f"--{boundary}--\r\n".encode())

    request = urllib.request.Request(
        BASE + path,
        data=body.getvalue(),
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        return response.status, response.headers, response.read()


def expect_error(path: str, payload: dict, label: str, status: int = 400):
    request = urllib.request.Request(
        BASE + path,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(request, timeout=60)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        check(label, exc.code == status and "error" in body, f"HTTP {exc.code} {body[:120]}")
        return
    check(label, False, "no error raised")


def make_fixtures() -> list[Path]:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    specs = [
        ("passport-1.png", (620, 880), (205, 225, 255)),
        ("passport-2.png", (880, 620), (255, 205, 205)),
        ("passport-10.png", (600, 840), (205, 255, 215)),
    ]
    paths = []
    for name, size, color in specs:
        target = FIXTURES / name
        if not target.exists():
            img = Image.new("RGB", size, color)
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(40, 40, 40), width=8)
            draw.text((30, 30), name, fill=(0, 0, 0))
            img.save(target)
        paths.append(target)
    return paths


def main() -> int:
    paths = make_fixtures()
    print(f"API base: {BASE}\nfixtures: {FIXTURES}\n")

    print("health")
    status, _, raw = get("/api/health")
    health = json.loads(raw)
    check("GET /api/health is 200", status == 200, str(status))
    check("health reports roots", isinstance(health.get("roots"), list) and len(health["roots"]) > 0)

    print("\nstatic UI")
    status, _, raw = get("/")
    check("GET / serves the UI", status == 200 and b"Image to PDF Converter" in raw)
    status, _, raw = get("/static/app.js")
    check("GET /static/app.js", status == 200 and b"exportPdf" in raw)
    status, _, raw = get("/static/styles.css")
    check("GET /static/styles.css", status == 200 and b".card" in raw)

    print("\nfolder listing")
    status, _, raw = get(f"/api/list?dir={urllib.parse.quote(str(FIXTURES))}&recursive=1")
    listing = json.loads(raw)
    names = [item["name"] for item in listing["images"]]
    check("lists the fixtures", status == 200 and len(names) == 3, str(names))
    check("natural name order", names == ["passport-1.png", "passport-2.png", "passport-10.png"], str(names))
    check("entries carry size and mtime", all(item.get("size") and item.get("mtime") for item in listing["images"]))

    status, _, raw = get(f"/api/subfolders?dir={urllib.parse.quote(str(FIXTURES.parent))}")
    folders = json.loads(raw)["folders"]
    check("subfolder listing finds 'passports'", any(f["name"] == "passports" for f in folders), str(folders))

    print("\nthumbnails")
    path = str(paths[0])
    status, headers, raw = get(f"/api/thumb?path={urllib.parse.quote(path)}&size=200")
    check("thumbnail is a JPEG", status == 200 and headers.get("Content-Type") == "image/jpeg")
    thumb = Image.open(io.BytesIO(raw))
    check("thumbnail respects max size", max(thumb.size) <= 200, str(thumb.size))

    status, _, raw = get(f"/api/thumb?path={urllib.parse.quote(path)}&size=200&rotation=90")
    rotated = Image.open(io.BytesIO(raw))
    check("rotated thumbnail is landscape", rotated.width > rotated.height, str(rotated.size))

    print("\nbuild from disk paths (JSON)")
    items = [{"path": str(p), "rotation": 0, "name": p.name} for p in paths]
    status, headers, raw = post_json(
        "/api/build",
        {
            "filename": "e2e-passports",
            "page": {"pageSize": "a4", "orientation": "auto", "fit": "contain", "marginMm": 6, "quality": 90},
            "items": items,
        },
    )
    check("POST /api/build is 200", status == 200, str(status))
    check("serves application/pdf", headers.get("Content-Type") == "application/pdf", headers.get("Content-Type") or "")
    check("attachment filename honoured", "e2e-passports.pdf" in (headers.get("Content-Disposition") or ""))
    check("X-Page-Count is 3", headers.get("X-Page-Count") == "3", headers.get("X-Page-Count") or "")
    reader = PdfReader(io.BytesIO(raw))
    check("PDF has 3 pages", len(reader.pages) == 3, str(len(reader.pages)))
    widths = [float(p.mediabox.width) for p in reader.pages]
    check(
        "orientation follows each image (page 2 is landscape)",
        widths[0] < float(reader.pages[0].mediabox.height) and widths[1] > float(reader.pages[1].mediabox.height),
        str([round(w) for w in widths]),
    )

    print("\nreordering changes the output")
    reordered = list(reversed(items))
    status, _, raw_rev = post_json(
        "/api/build",
        {"page": {"pageSize": "a4", "fit": "stretch"}, "items": reordered},
    )
    check("reordered build succeeds", status == 200 and len(PdfReader(io.BytesIO(raw_rev)).pages) == 3)
    first_color = None
    for image in PdfReader(io.BytesIO(raw_rev)).pages[0].images:
        img = Image.open(io.BytesIO(image.data)).convert("RGB")
        first_color = img.getpixel((img.width // 2, img.height // 2))
    check("reversed order puts the green page first", first_color and first_color[1] > first_color[0], str(first_color))

    print("\nbuild from uploaded bytes (multipart)")
    files = []
    for index, path in enumerate(paths):
        files.append((f"item-{index}", path.name, path.read_bytes()))
    status, headers, raw_up = post_multipart(
        "/api/build-multipart",
        {
            "items": json.dumps(
                [{"path": f"item-{i}", "name": p.name, "rotation": 90 if i == 1 else 0} for i, p in enumerate(paths)]
            ),
            "page": json.dumps({"pageSize": "letter", "fit": "contain"}),
            "filename": "uploaded",
        },
        files,
    )
    check("multipart build is 200", status == 200, str(status))
    up_reader = PdfReader(io.BytesIO(raw_up))
    check("multipart PDF has 3 Letter pages", len(up_reader.pages) == 3 and abs(float(up_reader.pages[0].mediabox.width) - 612) < 1)

    print("\nerror handling")
    expect_error("/api/build", {"items": []}, "empty item list is rejected")
    expect_error("/api/build", {"items": [{"path": "C:/nope/missing.png"}]}, "missing file is reported", 404)
    expect_error("/api/build", {"items": [{"name": "no path"}]}, "item without path is rejected")
    try:
        get("/api/list?dir=" + urllib.parse.quote("C:/definitely/not/here"))
        check("bad folder returns 404", False)
    except urllib.error.HTTPError as exc:
        check("bad folder returns 404", exc.code == 404, str(exc.code))
    try:
        get("/api/thumb?path=" + urllib.parse.quote("C:/nope.png"))
        check("missing thumbnail returns 404", False)
    except urllib.error.HTTPError as exc:
        check("missing thumbnail returns 404", exc.code == 404, str(exc.code))
    try:
        get("/api/nothing-here")
        check("unknown route returns 404", False)
    except urllib.error.HTTPError as exc:
        check("unknown route returns 404", exc.code == 404, str(exc.code))

    print(f"\n{PASSED} check(s) passed, {len(FAILED)} failed.")
    for label in FAILED:
        print(f"  - {label}")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
