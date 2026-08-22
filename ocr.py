"""Text recognition for slides that are pictures rather than text.

A lecture PDF exported the wrong way, or one slide holding a screenshot of a
table, has no text layer at all: pdfium reports nothing and Detect has
nothing to box. macOS carries a text recogniser in the Vision framework, and
it can be reached without any dependency at all — `osascript -l JavaScript`
has an Objective-C bridge, so the whole thing is a script string and a
subprocess. Nothing is bundled and nothing is installed.

macOS only. Everywhere else `available()` is False and Detect behaves exactly
as it did before, which is the honest outcome: the cross-platform engines are
all either a user-installed binary or a Python-ABI-specific wheel, and a
wheel per Python version would undo what vendoring pypdfium2 buys us (see
pdf_renderer).

First call on a machine takes ~30s while macOS loads its models; every call
after is a fraction of a second, so `warm_up()` gets that out of the way in
the background when a PDF is opened.
"""
import json
import os
import subprocess
import sys
import tempfile
from typing import Optional

from aqt.qt import QImage


class OcrError(RuntimeError):
    pass


# Vision hands back one observation per line, but Detect boxes words, so
# each line is walked and boundingBoxForRange asked for the box of every
# run of non-space characters in it. boundingBox is normalised with a
# bottom-left origin; the caller flips it, because only the caller knows
# the image height.
_JXA = r"""
ObjC.import('Vision');
ObjC.import('Foundation');

function run(argv) {
    var handler = $.VNImageRequestHandler.alloc.initWithURLOptions(
        $.NSURL.fileURLWithPath($(argv[0])), $());
    var req = $.VNRecognizeTextRequest.alloc.init;
    req.recognitionLevel = 0;          // accurate; fast is worse and barely quicker
    req.usesLanguageCorrection = true;
    handler.performRequestsError($([req]), Ref());
    var out = [];
    var results = req.results;
    if (results) {
        for (var i = 0; i < results.count; i++) {
            var obs = results.objectAtIndex(i);
            var cands = obs.topCandidates(1);
            if (!cands || cands.count === 0) { continue; }
            var top = cands.objectAtIndex(0);
            var s = ObjC.unwrap(top.string);
            var j = 0;
            while (j < s.length) {
                while (j < s.length && /\s/.test(s[j])) { j++; }
                var start = j;
                while (j < s.length && !/\s/.test(s[j])) { j++; }
                if (j <= start) { break; }
                var ro = top.boundingBoxForRangeError(
                    $.NSMakeRange(start, j - start), Ref());
                if (!ro) { continue; }
                var bb = ro.boundingBox;
                out.push({t: s.substring(start, j), c: top.confidence,
                          x: bb.origin.x, y: bb.origin.y,
                          w: bb.size.width, h: bb.size.height});
            }
        }
    }
    return JSON.stringify(out);
}
"""

_MIN_CONF = 0.3     # below this Vision is guessing at noise
_script: Optional[str] = None


def available() -> bool:
    """Can this machine recognise text in an image?"""
    return sys.platform == "darwin" and os.path.exists("/usr/bin/osascript")


def _script_file() -> str:
    """The JXA on disk. Written once per Anki session, into Qt's temp dir."""
    global _script
    if _script and os.path.exists(_script):
        return _script
    fd, path = tempfile.mkstemp(suffix=".js", prefix="pdfocc_ocr_")
    with os.fdopen(fd, "w") as fh:
        fh.write(_JXA)
    _script = path
    return path


def recognize(img: QImage, timeout: float = 30.0) -> list:
    """Read the text in an image. Returns one (x, y, w, h, text) per word.

    Coordinates are top-left origin, matching everything else the add-on
    passes around. Raises OcrError if the recogniser could not be run at all;
    an image with no readable text is an empty list, not an error.
    """
    if not available():
        raise OcrError("Text recognition needs macOS.")
    if img.isNull() or img.width() < 4 or img.height() < 4:
        return []

    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="pdfocc_ocr_")
        os.close(fd)
        if not img.save(tmp, "PNG"):
            raise OcrError("Could not write the image out for recognition.")
        try:
            proc = subprocess.run(
                ["/usr/bin/osascript", "-l", "JavaScript", _script_file(), tmp],
                capture_output=True, text=True, timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            raise OcrError(
                "Text recognition timed out. The very first run on a machine "
                "loads macOS's models and can take half a minute — try again."
            )
        if proc.returncode != 0:
            raise OcrError((proc.stderr or "osascript failed").strip())
        try:
            obs = json.loads(proc.stdout or "[]")
        except ValueError:
            raise OcrError("Could not read the recogniser's output.")
    finally:
        if tmp and os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass

    w_px, h_px = float(img.width()), float(img.height())
    out = []
    for o in obs:
        try:
            if float(o["c"]) < _MIN_CONF:
                continue
            x = float(o["x"]) * w_px
            # Vision's origin is bottom-left, the image's is top-left
            y = (1.0 - float(o["y"]) - float(o["h"])) * h_px
            out.append((x, y, float(o["w"]) * w_px, float(o["h"]) * h_px,
                        o.get("t", "")))
        except (KeyError, TypeError, ValueError):
            continue
    out.sort(key=lambda r: (round(r[1]), r[0]))
    return out


def warm_up() -> None:
    """Nudge macOS into loading its models, without waiting for it.

    Fire and forget: nothing here reads the result or reports a failure,
    because the only thing it buys is that the first real call is quick.
    """
    if not available():
        return
    img = QImage(64, 32, QImage.Format.Format_RGB32)
    img.fill(0xFFFFFF)
    fd, tmp = tempfile.mkstemp(suffix=".png", prefix="pdfocc_warm_")
    os.close(fd)
    if not img.save(tmp, "PNG"):
        return
    try:
        subprocess.Popen(
            ["/usr/bin/osascript", "-l", "JavaScript", _script_file(), tmp],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass
