#!/usr/bin/env python3
"""
Build pdf_occlusion.ankiaddon with pypdfium2 vendored for every platform.

Downloads the pypdfium2 wheel for each supported OS/arch, unpacks it into
vendor/<platform_tag>/, then zips the add-on. pypdfium2 binds pdfium via
ctypes, so the wheels are Python-version independent — only the OS/arch
matters, which is what makes one .ankiaddon work on every Anki build.

Usage:  python3 build.py
Requires: pip (any Python >= 3.9)
"""
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
VENDOR = ROOT / "vendor"
OUT = ROOT / "pdf_occlusion.ankiaddon"

# platform tag used in __init__.py -> pip --platform value
PLATFORMS = {
    "mac_arm64": "macosx_11_0_arm64",
    "mac_x86_64": "macosx_10_13_x86_64",
    "win_amd64": "win_amd64",
    "win_arm64": "win_arm64",
    "linux_x86_64": "manylinux_2_17_x86_64",
    "linux_aarch64": "manylinux_2_17_aarch64",
}

ADDON_FILES = [
    "__init__.py",
    "pdf_renderer.py",
    "pdf_occlusion_dialog.py",
    "occlusion_canvas.py",
    "card_builder.py",
    "cloze_dialog.py",
    "ocr.py",
    "session_store.py",
    "config.json",
    "config.md",
    "manifest.json",
    "icon.svg",
    "caret.svg",
    "caret_muted.svg",
]


def vendor_pypdfium2() -> None:
    if VENDOR.exists():
        shutil.rmtree(VENDOR)
    for tag, pip_platform in PLATFORMS.items():
        dest = VENDOR / tag
        dest.mkdir(parents=True)
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(
                [
                    sys.executable, "-m", "pip", "download", "pypdfium2",
                    "--platform", pip_platform,
                    "--only-binary=:all:", "--no-deps", "-d", tmp,
                ],
                check=True,
            )
            wheel = next(Path(tmp).glob("*.whl"))
            with zipfile.ZipFile(wheel) as zf:
                zf.extractall(dest)
        # drop wheel metadata
        for distinfo in dest.glob("*.dist-info"):
            shutil.rmtree(distinfo)
        print(f"  vendored {tag}")


def build_addon() -> None:
    if OUT.exists():
        OUT.unlink()
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in ADDON_FILES:
            zf.write(ROOT / name, name)
        for f in sorted(VENDOR.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                zf.write(f, f.relative_to(ROOT))
    print(f"built {OUT.name} ({OUT.stat().st_size / 1e6:.1f} MB)")


if __name__ == "__main__":
    print("Vendoring pypdfium2 wheels…")
    vendor_pypdfium2()
    build_addon()
