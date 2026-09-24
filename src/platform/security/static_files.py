"""Safe static-file resolution for the SPA fallback route."""

from __future__ import annotations

import os


def resolve_static_file(static_root: str, request_path: str) -> str:
    """Map a request path to a file inside ``static_root``; anything else gets index.html.

    Resolves symlinks and ``..`` segments (including percent-encoded ones, which Starlette
    decodes before routing) and refuses any path that escapes the static root.
    """
    root = os.path.realpath(static_root)
    candidate = os.path.realpath(os.path.join(root, request_path.lstrip("/\\")))
    if os.path.commonpath([root, candidate]) == root and os.path.isfile(candidate):
        return candidate
    return os.path.join(root, "index.html")
