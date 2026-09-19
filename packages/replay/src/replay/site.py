"""Serving the built frontend: one function for the local server and the Lambda.

A request path is a file under the site root, or - if it names no file - one of
the app's own routes, which get index.html (the SPA routes itself: /runs/qwen-00,
/diff?a=...). A path that would leave the root, `..` encoded or not, is a 404.

Hashed build output under /assets/ never changes under the same name, so it is
cached for a year; index.html names those files, so it is never cached.
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass
from urllib.parse import unquote

TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".mjs": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".txt": "text/plain; charset=utf-8",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".ico": "image/x-icon",
    ".webp": "image/webp",
}
TEXT = ("text/", "application/json", "image/svg+xml")
IMMUTABLE = "public, max-age=31536000, immutable"
NO_CACHE = "no-cache"


@dataclass(frozen=True)
class File:
    status: int
    content_type: str
    body: bytes
    cache_control: str | None = None

    @property
    def is_text(self) -> bool:
        return self.content_type.startswith(TEXT)


NOT_FOUND = File(404, "application/json", b'{"error": "not found"}')


def serve(root: pathlib.Path, path: str) -> File:
    """The response for GET `path` from the built site at `root`."""
    root = root.resolve()
    decoded = unquote(unquote(path.split("?", 1)[0]))
    parts = [p for p in decoded.replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        return NOT_FOUND
    target = root.joinpath(*parts).resolve() if parts else root / "index.html"
    if target != root and root not in target.parents:
        return NOT_FOUND
    if target.is_file():
        kind = TYPES.get(target.suffix.lower(), "application/octet-stream")
        cache = IMMUTABLE if parts[:1] == ["assets"] else (NO_CACHE if target.name == "index.html" else None)
        return File(200, kind, target.read_bytes(), cache)
    if parts and "." in parts[-1]:
        return NOT_FOUND  # a missing file, not an app route
    index = root / "index.html"
    if not index.is_file():
        return NOT_FOUND
    return File(200, TYPES[".html"], index.read_bytes(), NO_CACHE)
