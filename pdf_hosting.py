"""Publish the Slides / Notes PDFs so a card can open them on any device.

Out of the box a card's "Slides PDF" / "Notes PDF" fields hold the path of
the PDF on this computer: nothing leaves the machine, and the buttons only
work in desktop Anki. Fill in "pdf_hosting" in the config and the PDFs are
copied into a folder you serve on the web (typically a clone of a GitHub
Pages repo) and the cards get the public URL instead, which AnkiMobile,
AnkiDroid and AnkiWeb can all open.

The add-on never sees a password, token or key. It runs plain `git` in your
folder, so it pushes with whatever that clone already pushes with (SSH key,
keychain, gh's credential helper): if `git push` works there from a
terminal, it works from here. If it doesn't, the cards are still made with
their URLs, the commit waits locally, and the next push that does go
through publishes it. A folder that isn't a git clone is just copied into.

No Anki imports here, so this can be exercised outside Anki.
"""
import hashlib
import os
import re
import shutil
import subprocess
import threading
from typing import Any, Optional

_URL_RE = re.compile(r"^https?://", re.IGNORECASE)
_UNSAFE_RE = re.compile(r"[^A-Za-z0-9._-]+")
_GIT_TIMEOUT = 120  # seconds, per git command
_git_lock = threading.Lock()  # one Create All Cards' push at a time


class PublishError(Exception):
    pass


def is_url(location: str) -> bool:
    return bool(_URL_RE.match(location or ""))


def settings(raw: Optional[dict]) -> Optional[dict]:
    """The "pdf_hosting" config, normalised — or None when it's switched off.

    Off unless both the folder and the URL it is served at are filled in.
    """
    raw = raw if isinstance(raw, dict) else {}
    folder = os.path.expanduser(str(raw.get("folder") or "").strip())
    base_url = str(raw.get("base_url") or "").strip()
    if not folder or not is_url(base_url):
        return None
    return {
        "folder": os.path.abspath(folder),
        "base_url": base_url.rstrip("/") + "/",
        "git_push": bool(raw.get("git_push", True)),
    }


def _published_name(path: str) -> str:
    """URL-safe file name carrying a hash of the content.

    The hash makes publishing idempotent (the same PDF always lands on the
    same URL) and means a revised deck exported under the same name gets a
    new file instead of changing what older cards open.
    """
    sha = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    stem = os.path.splitext(os.path.basename(path))[0]
    slug = _UNSAFE_RE.sub("-", stem).strip("-._")[:80] or "document"
    return f"{slug}-{sha.hexdigest()[:10]}.pdf"


def publish(location: str, hosting: dict) -> str:
    """The URL for the PDF at `location`, copying it into the folder first.

    A location that is already a URL — a card relinked earlier, a notes PDF
    recovered from one — comes back unchanged. Raises OSError if the PDF
    can't be read or copied.
    """
    if not location or is_url(location):
        return location
    name = _published_name(location)
    dest = os.path.join(hosting["folder"], name)
    if not os.path.exists(dest):
        os.makedirs(hosting["folder"], exist_ok=True)
        tmp = dest + ".tmp"
        shutil.copyfile(location, tmp)
        os.replace(tmp, dest)
    return hosting["base_url"] + name


def local_copy(url: str, hosting: Optional[dict]) -> str:
    """The file in the hosting folder that `url` serves, or "" if there's none."""
    if not hosting or not url.startswith(hosting["base_url"]):
        return ""
    name = url[len(hosting["base_url"]):]
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return ""
    path = os.path.join(hosting["folder"], name)
    return path if os.path.isfile(path) else ""


def local_pdf(location: str, hosting: Optional[dict]) -> str:
    """A path that can be opened on this machine for a field's location.

    Paths pass through; a URL maps to its copy in the hosting folder, or to
    "" when this machine doesn't have one.
    """
    return local_copy(location, hosting) if is_url(location) else location


# ------------------------------------------------------------------- git

def _git(folder: str, *args: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    # Anki started from the Dock/Start menu gets a bare PATH; git-lfs and
    # credential helpers installed by Homebrew live outside it.
    env["PATH"] = os.pathsep.join(
        p for p in (env.get("PATH", ""), "/opt/homebrew/bin", "/usr/local/bin") if p)
    env["GIT_TERMINAL_PROMPT"] = "0"  # fail, don't wait for a password nobody sees
    kwargs: dict[str, Any] = {}
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        return subprocess.run(
            ["git", "-C", folder, *args], env=env, capture_output=True,
            text=True, timeout=_GIT_TIMEOUT, **kwargs)
    except FileNotFoundError:
        raise PublishError("git isn't installed, or isn't on Anki's PATH.")
    except subprocess.TimeoutExpired:
        raise PublishError(f"git {args[0]} gave no answer for {_GIT_TIMEOUT}s.")


def _check(res: subprocess.CompletedProcess, what: str) -> None:
    if res.returncode != 0:
        detail = (res.stderr or res.stdout).strip().splitlines()[-3:]
        raise PublishError(f"{what} failed:\n" + "\n".join(detail))


def push(hosting: dict, urls: list) -> bool:
    """Commit the published PDFs behind `urls` and push them.

    Commits only those files, whatever else is staged in the clone. Also
    pushes commits left behind by an earlier push that failed. Returns True
    if something was pushed, False if there was nothing to do (or the folder
    isn't a git clone). Raises PublishError. Blocking — run it off the main
    thread.
    """
    folder = hosting["folder"]
    files = sorted({p for p in (local_copy(u, hosting) for u in urls) if p})
    with _git_lock:
        inside = _git(folder, "rev-parse", "--is-inside-work-tree")
        if inside.returncode != 0 or inside.stdout.strip() != "true":
            return False

        committed = False
        if files:
            _check(_git(folder, "add", "--", *files), "git add")
            status = _git(folder, "status", "--porcelain", "--", *files)
            _check(status, "git status")
            if status.stdout.strip():
                n = len(files)
                subject = f"Add {n} PDF{'s' if n != 1 else ''} from PDF Occlusion"
                body = "\n".join(os.path.basename(f) for f in files)
                _check(_git(folder, "commit", "-m", subject, "-m", body,
                            "--", *files), "git commit")
                committed = True

        if not committed:
            ahead = _git(folder, "rev-list", "--count", "@{upstream}..HEAD")
            if ahead.returncode == 0 and ahead.stdout.strip() == "0":
                return False

        if _git(folder, "push").returncode != 0:
            # Most likely the remote moved on (pushed from elsewhere). The
            # commit only adds new files, so replaying it can't conflict
            # with anything but the user's own unpushed work — and if it
            # does, put their clone back the way it was rather than leave
            # it mid-rebase.
            pull = _git(folder, "pull", "--rebase", "--autostash")
            if pull.returncode != 0:
                _git(folder, "rebase", "--abort")
                _check(pull, "git pull")
            _check(_git(folder, "push"), "git push")
        return True
