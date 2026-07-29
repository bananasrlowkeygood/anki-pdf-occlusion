"""
Per-PDF session persistence.

Each opened PDF gets one JSON file in user_files/sessions/ (user_files/ is
preserved across add-on updates). A session stores everything needed to
resume work — boxes, skipped slides, per-slide mode overrides, lecture name —
plus the region→note-id map that lets "Create All Cards" update existing
cards in place instead of duplicating them.

Files are keyed by a hash of the absolute PDF path; the path itself is stored
inside the file so a hash collision can never resurrect the wrong session.
"""
import hashlib
import json
import os
import time
from typing import Optional

_DIR = os.path.join(os.path.dirname(__file__), "user_files", "sessions")

VERSION = 1


def _path_for(pdf_path: str) -> str:
    key = hashlib.sha1(os.path.abspath(pdf_path).encode("utf-8")).hexdigest()[:20]
    return os.path.join(_DIR, f"{key}.json")


def load(pdf_path: str) -> Optional[dict]:
    try:
        with open(_path_for(pdf_path), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if data.get("pdf_path") != os.path.abspath(pdf_path):
        return None
    return data


def save(pdf_path: str, data: dict) -> None:
    try:
        os.makedirs(_DIR, exist_ok=True)
        payload = dict(
            data,
            pdf_path=os.path.abspath(pdf_path),
            saved_at=int(time.time()),
            version=VERSION,
        )
        tmp = _path_for(pdf_path) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        os.replace(tmp, _path_for(pdf_path))
    except OSError:
        pass  # never let a failed save break the dialog


def delete(pdf_path: str) -> None:
    try:
        os.remove(_path_for(pdf_path))
    except OSError:
        pass


def find_note(nid: int) -> Optional[tuple]:
    """Locate a note across all sessions: (pdf_path, region_key) or None.

    Lets the editor button jump straight from a card in Browse to the
    slide and box that produced it."""
    try:
        names = os.listdir(_DIR)
    except OSError:
        return None
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for key, mapped in (data.get("note_map") or {}).items():
            if mapped == nid and data.get("pdf_path"):
                return data["pdf_path"], key
    return None


def list_sessions(limit: int = 8) -> list[dict]:
    """Most recent sessions, newest first, for the Recent menu."""
    out = []
    try:
        names = os.listdir(_DIR)
    except OSError:
        return out
    for name in names:
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(_DIR, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        if data.get("pdf_path"):
            out.append(data)
    out.sort(key=lambda d: d.get("saved_at", 0), reverse=True)
    return out[:limit]
