from __future__ import annotations
from pathlib import Path
import tempfile
import shutil
import requests

# Where the project keeps a canonical TTF we control
PKG_DIR = Path(__file__).resolve().parent
FONTS_DIR = PKG_DIR / "fonts"
TTF_PATH = FONTS_DIR / "UnicodeSans.ttf"

# A couple of reliable Unicode TTF sources
FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans-Regular.ttf",
    "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
]

def _download_ttf(dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    last_err = None
    for url in FONT_URLS:
        try:
            r = requests.get(url, timeout=30, headers={"User-Agent": "python-requests"})
            r.raise_for_status()
            data = r.content
            # cheap sanity check: TrueType/OTF magic
            if len(data) >= 4 and data[:4] in (b"\x00\x01\x00\x00", b"OTTO", b"true", b"typ1"):
                dst.write_bytes(data)
                return
            last_err = f"Invalid font bytes from {url}"
        except Exception as e:
            last_err = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"Could not fetch a Unicode TTF. Last error: {last_err}")

def ensure_unicode_ttf() -> Path:
    """Ensure a Unicode TTF exists at TTF_PATH; return that path."""
    if not TTF_PATH.exists() or TTF_PATH.stat().st_size < 1024:
        _download_ttf(TTF_PATH)
    return TTF_PATH

def register_pdf_font(pdf) -> str:
    """
    Register the Unicode font with FPDF from a TEMPORARY COPY of the TTF.
    This avoids FPDF trying to reuse any stale .pkl cache you might have near the repo file.
    Returns the temp TTF path used.
    """
    ttf = ensure_unicode_ttf()
    tmpdir = Path(tempfile.mkdtemp(prefix="fpdf_font_"))
    tmp_ttf = tmpdir / "UnicodeSans.ttf"
    shutil.copyfile(ttf, tmp_ttf)
    # FPDF v1 API: add_font(name, style, fname, uni=True)
    pdf.add_font("Unicode", "", str(tmp_ttf), uni=True)
    pdf.set_font("Unicode", size=12)
    return str(tmp_ttf)