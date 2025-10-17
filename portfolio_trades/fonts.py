from pathlib import Path
import io, requests
from fontTools.ttLib import TTFont

FONT_DIR = Path(__file__).resolve().parent / "fonts_cache"
FONT_PATH = FONT_DIR / "UnicodeSans.ttf"
FONT_URLS = [
    "https://github.com/google/fonts/raw/main/ofl/notosans/NotoSans-Regular.ttf",
    "https://github.com/dejavu-fonts/dejavu-fonts/raw/master/ttf/DejaVuSans.ttf",
    "https://github.com/adobe-fonts/source-sans/raw/release/TTF/SourceSans3-Regular.ttf",
]

def _valid_ttf_bytes(b: bytes) -> bool:
    if len(b) < 4: return False
    if b[:4] not in (b"\x00\x01\x00\x00", b"true", b"typ1", b"OTTO"): return False
    TTFont(io.BytesIO(b))
    return True

def ensure_unicode_font() -> str:
    FONT_DIR.mkdir(parents=True, exist_ok=True)
    if FONT_PATH.exists():
        try:
            TTFont(str(FONT_PATH)); return str(FONT_PATH)
        except Exception:
            pass
    last = None
    for url in FONT_URLS:
        try:
            r = requests.get(url, timeout=20); r.raise_for_status()
            if _valid_ttf_bytes(r.content):
                with open(FONT_PATH, "wb") as f: f.write(r.content)
                return str(FONT_PATH)
            last = f"Invalid font at {url}"
        except Exception as e:
            last = f"{type(e).__name__}: {e}"
    raise RuntimeError(f"Failed to fetch Unicode font. Last error: {last}")