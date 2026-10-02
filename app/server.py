"""Local web server for the Image to PDF converter.

Standard library only (plus Pillow/pypdf for the actual PDF work), so it runs
with a bare Python install. Serves the UI in ``web/`` and a small JSON API.
"""

from __future__ import annotations

import io
import json
import mimetypes
import os
import re
import shutil
import socket
import sys
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from PIL import Image, ImageOps

from .images import (
    MAX_SCAN_RESULTS,
    is_supported_image,
    list_images,
    list_subfolders,
    natural_key,
    root_candidates,
)
from .pdfbuild import PageSettings, build_pdf, parse_items

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
UPLOAD_DIR = Path(os.environ.get("TEMP", ".")) / "image-to-pdf-uploads"

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512 MB across one batch
THUMB_CACHE: dict[tuple, bytes] = {}
THUMB_CACHE_LIMIT = 400
THUMB_LOCK = threading.Lock()

STATIC_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
}


def _pick_folder_dialog(initial: str | None = None) -> str | None:
    """Open the OS folder picker on the machine running this server."""
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None

    result: dict[str, str | None] = {"path": None}

    def run() -> None:
        root = None
        try:
            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            chosen = filedialog.askdirectory(
                title="Choose a folder of passport images",
                initialdir=initial if initial and os.path.isdir(initial) else str(Path.home()),
                mustexist=True,
            )
            result["path"] = chosen or None
        except Exception:
            result["path"] = None
        finally:
            try:
                if root is not None:
                    root.destroy()
            except Exception:
                pass

    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    thread.join(timeout=600)
    return result["path"]


def _thumb_bytes(path: str, size: int, rotation: int = 0) -> bytes:
    key = (path, size, rotation)
    with THUMB_LOCK:
        cached = THUMB_CACHE.get(key)
    if cached is not None:
        return cached

    img = Image.open(path)
    img.load()
    img = ImageOps.exif_transpose(img)
    if rotation:
        img = img.rotate(-rotation, expand=True)
    if img.mode not in ("RGB", "L"):
        background = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
            rgba = img.convert("RGBA")
            background.paste(rgba, mask=rgba.split()[-1])
        else:
            background.paste(img.convert("RGB"))
        img = background
    img.thumbnail((size, size), Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    img.save(buffer, "JPEG", quality=80)
    data = buffer.getvalue()
    img.close()

    with THUMB_LOCK:
        if len(THUMB_CACHE) >= THUMB_CACHE_LIMIT:
            THUMB_CACHE.clear()
        THUMB_CACHE[key] = data
    return data


class UploadedFile:
    """One file part of a multipart upload."""

    __slots__ = ("name", "filename", "content_type", "data")

    def __init__(self, name: str, filename: str, content_type: str, data: bytes):
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data


def _split_multipart(raw: bytes, content_type: str) -> tuple[dict, list[UploadedFile]]:
    """Parse multipart/form-data without the ``cgi`` module (gone in 3.13+)."""
    match = re.search(r'boundary="?([^";]+)"?', content_type or "")
    if not match:
        raise ValueError("Malformed multipart request: no boundary.")

    delimiter = b"--" + match.group(1).strip().encode("latin-1")
    fields: dict[str, str] = {}
    files: list[UploadedFile] = []

    for chunk in raw.split(delimiter):
        if not chunk or chunk in (b"--", b"--\r\n", b"\r\n"):
            continue
        chunk = chunk.lstrip(b"\r\n")
        split_at = chunk.find(b"\r\n\r\n")
        if split_at == -1:
            continue

        raw_headers = chunk[:split_at].decode("utf-8", "replace")
        body = chunk[split_at + 4:]
        # The CRLF immediately before the next boundary belongs to the framing.
        if body.endswith(b"\r\n"):
            body = body[:-2]
        elif body.endswith(b"\r\n--"):
            body = body[:-4]

        disposition = ""
        part_type = "application/octet-stream"
        for line in raw_headers.splitlines():
            if line.lower().startswith("content-disposition:"):
                disposition = line.split(":", 1)[1].strip()
            elif line.lower().startswith("content-type:"):
                part_type = line.split(":", 1)[1].strip()

        name = _header_param(disposition, "name")
        filename = _header_param(disposition, "filename")
        if filename is not None:
            files.append(
                UploadedFile(name or "file", os.path.basename(filename), part_type, body)
            )
        elif name:
            fields[name] = body.decode("utf-8", "replace")
    return fields, files


def _header_param(header: str, key: str) -> str | None:
    match = re.search(rf'{key}="([^"]*)"', header or "", flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(rf"{key}=([^;]+)", header or "", flags=re.IGNORECASE)
    return match.group(1).strip() if match else None


class Handler(BaseHTTPRequestHandler):
    server_version = "ImageToPDF/1.0"
    protocol_version = "HTTP/1.1"

    # ---------------------------------------------------------------- helpers
    def log_message(self, fmt, *args):  # quieter console
        if os.environ.get("IMAGE_TO_PDF_VERBOSE"):
            super().log_message(fmt, *args)

    def _send(self, status: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
                pass

    def _json(self, payload, status: int = 200):
        body = json.dumps(payload, default=str).encode("utf-8")
        self._send(status, body, "application/json; charset=utf-8")

    def _error(self, message: str, status: int = 400):
        self._json({"error": message}, status)

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            length = 0
        if length <= 0:
            return b""
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("Upload is larger than the 512 MB limit.")
        chunks = []
        remaining = length
        while remaining > 0:
            chunk = self.rfile.read(min(remaining, 1 << 20))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _load_json(body: bytes) -> dict:
        if not body:
            return {}
        try:
            data = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Request body must be a JSON object.")
        return data

    # ------------------------------------------------------------------ verbs
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        query = parse_qs(parsed.query)

        try:
            if route in ("/", "/index.html"):
                return self._serve_static("index.html")
            if route.startswith("/static/"):
                return self._serve_static(route[len("/static/"):])
            if route == "/api/health":
                return self._json(
                    {
                        "ok": True,
                        "app": "Image to PDF Converter",
                        "roots": root_candidates(),
                        "nativeFolderPicker": _tk_available(),
                        "supportedExtensions": sorted(
                            {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
                        ),
                    }
                )
            if route == "/api/thumb":
                return self._thumb(query)
            if route == "/api/list":
                return self._list(query)
            if route == "/api/subfolders":
                return self._subfolders(query)
            if route == "/api/download":
                return self._error("Use POST /api/build to create a PDF.", 405)
            return self._error("Unknown route.", 404)
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        route = unquote(parsed.path)
        try:
            if route == "/api/pick-folder":
                chosen = _pick_folder_dialog()
                return self._json({"path": chosen, "cancelled": chosen is None})
            if route == "/api/build":
                return self._build()
            if route == "/api/build-multipart":
                return self._build_multipart()
            if route == "/api/reset":
                shutil.rmtree(UPLOAD_DIR, ignore_errors=True)
                with THUMB_LOCK:
                    THUMB_CACHE.clear()
                return self._json({"ok": True})
            return self._error("Unknown route.", 404)
        except ValueError as exc:
            return self._error(str(exc), 400)
        except FileNotFoundError as exc:
            return self._error(str(exc), 404)
        except Exception as exc:  # pragma: no cover - defensive
            return self._error(f"{type(exc).__name__}: {exc}", 500)

    # --------------------------------------------------------------- handlers
    def _serve_static(self, relative: str):
        target = (WEB_DIR / relative).resolve()
        if not str(target).startswith(str(WEB_DIR.resolve())) or not target.is_file():
            return self._error("Not found.", 404)
        ctype = STATIC_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(str(target))[0] or "application/octet-stream"
        self._send(200, target.read_bytes(), ctype)

    def _thumb(self, query):
        path = (query.get("path") or [""])[0]
        try:
            size = max(48, min(1600, int((query.get("size") or ["240"])[0])))
        except ValueError:
            size = 240
        try:
            rotation = int((query.get("rotation") or ["0"])[0]) % 360
        except ValueError:
            rotation = 0
        if not path or not os.path.isfile(path):
            return self._error("Image not found.", 404)
        try:
            data = _thumb_bytes(path, size, rotation)
        except Exception as exc:
            return self._error(f"Could not read image: {exc}", 415)
        self._send(200, data, "image/jpeg", {"Cache-Control": "private, max-age=300"})

    def _list(self, query):
        directory = (query.get("dir") or [""])[0]
        recursive = (query.get("recursive") or ["1"])[0] not in ("0", "false", "no")
        if not directory:
            return self._error("Missing 'dir' parameter.", 400)
        directory = str(Path(directory).expanduser())
        if not os.path.isdir(directory):
            return self._error(f"Not a folder: {directory}", 404)
        try:
            images = list_images(directory, recursive=recursive)
        except PermissionError:
            return self._error(f"Permission denied: {directory}", 403)
        return self._json(
            {
                "dir": directory,
                "recursive": recursive,
                "count": len(images),
                "truncated": len(images) >= MAX_SCAN_RESULTS,
                "images": images,
            }
        )

    def _subfolders(self, query):
        directory = (query.get("dir") or [""])[0]
        if not directory:
            return self._error("Missing 'dir' parameter.", 400)
        try:
            return self._json({"dir": directory, "folders": list_subfolders(directory)})
        except PermissionError:
            return self._error(f"Permission denied: {directory}", 403)
        except NotADirectoryError as exc:
            return self._error(str(exc), 404)

    def _resolve_page_settings(self, payload: dict) -> PageSettings:
        return PageSettings.from_payload(payload.get("page") or payload.get("pageSettings") or {})

    def _build(self):
        payload = self._load_json(self._read_body())
        items = parse_items(payload.get("items") or [])
        settings = self._resolve_page_settings(payload)
        if not items:
            return self._error("Add at least one image before exporting.", 400)
        started = time.perf_counter()
        data = build_pdf(items, settings)
        filename = _safe_filename(payload.get("filename") or "images")
        self._send(
            200,
            data,
            "application/pdf",
            {
                "Content-Disposition": f'attachment; filename="{filename}.pdf"',
                "X-Page-Count": str(len(items)),
                "X-Build-Seconds": f"{time.perf_counter() - started:.2f}",
            },
        )

    def _build_multipart(self):
        body = self._read_body()
        if not body:
            return self._error("Empty upload.", 400)
        fields, files = _split_multipart(body, self.headers.get("Content-Type", ""))

        try:
            items = parse_items(fields.get("items") or [])
        except ValueError as exc:
            return self._error(str(exc), 400)
        if not items:
            return self._error("Add at least one image before exporting.", 400)

        upload_dir = UPLOAD_DIR / uuid.uuid4().hex
        upload_dir.mkdir(parents=True, exist_ok=True)
        stored: dict[str, str] = {}
        for upload in files:
            target = _unique_path(upload_dir / os.path.basename(upload.filename or "image"))
            target.write_bytes(upload.data)
            # The browser names each file field after its client-side page id,
            # which keeps duplicate basenames from colliding.
            stored[upload.name] = str(target)
            stored.setdefault(upload.filename, str(target))
            stored.setdefault(os.path.basename(upload.filename or ""), str(target))

        for item in items:
            local = stored.get(item.path) or stored.get(os.path.basename(item.path))
            if not local:
                raise ValueError(f"Uploaded file missing for '{item.path}'.")
            item.path = local

        try:
            page_payload = json.loads(fields.get("page") or "{}")
        except json.JSONDecodeError:
            page_payload = {}
        settings = PageSettings.from_payload(page_payload)
        data = build_pdf(items, settings)
        filename = _safe_filename(fields.get("filename") or "images")
        self._send(
            200,
            data,
            "application/pdf",
            {
                "Content-Disposition": f'attachment; filename="{filename}.pdf"',
                "X-Page-Count": str(len(items)),
            },
        )


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(1, 10000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    return path.with_name(f"{stem}-{uuid.uuid4().hex[:8]}{suffix}")


def _safe_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", str(name)).strip(" .")
    cleaned = re.sub(r"\.pdf$", "", cleaned, flags=re.IGNORECASE)
    return cleaned[:120] or "images"


def _tk_available() -> bool:
    try:
        import tkinter  # noqa: F401

        return True
    except Exception:
        return False


def _free_port(preferred: int, host: str = "127.0.0.1") -> int:
    for candidate in [preferred] + list(range(preferred + 1, preferred + 25)):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                probe.bind((host, candidate))
            except OSError:
                continue
            return candidate
    raise SystemExit(f"No free port near {preferred}.")


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    if not WEB_DIR.is_dir():
        raise SystemExit(f"UI assets missing at {WEB_DIR}")
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    actual_port = _free_port(port, host)
    httpd = ThreadingHTTPServer((host, actual_port), Handler)
    httpd.daemon_threads = True
    url = f"http://{host}:{actual_port}/"
    print("Image to PDF Converter is running.")
    print(f"  Open: {url}")
    print("  Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    sys.exit(serve())
