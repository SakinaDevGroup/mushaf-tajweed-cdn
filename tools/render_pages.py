"""
Render the KFGQPC V4 Tajweed Mushaf into page images (PNG).

Рендер мусҳафа KFGQPC V4 Tajweed в картинки страниц (PNG).

The pages are drawn by the same HTML engine the Sakina app uses
(`KFGQPC_V4_layout`: index.html + 604 per-page COLR/CPAL colour fonts +
script/quran_pages.json), opened in headless Chrome through Playwright.
Light and dark themes are just different palettes of the same fonts:

    light -> palette 0 (tajweed colours on a white page)
    dark  -> palette 1 (light letters + tajweed colours on a dark page)

Usage / Запуск:
    python -m pip install playwright pillow
    python tools/render_pages.py --engine <path to KFGQPC_V4_layout> [--themes light dark]
                                 [--pages 1-604] [--width 1080] [--out .]

Every page is rendered on a sheet of the same size (width x height), with
the text block centred vertically, so all 604 pages share one scale.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import io
import sys
import threading
import time
from pathlib import Path

from PIL import Image
from playwright.sync_api import sync_playwright

THEMES = {
    # name: (palette, background, colour of surah-name / basmala lines)
    "light": (0, "#ffffff", "#1a1200"),
    "dark": (1, "#0d0f12", "#ece6da"),
}

# Width / height of every sheet (1:2, i.e. 1080 x 2160). The tallest pages
# (601-604: three surah headers each) fit with a margin; every other page is
# centred on the same sheet, so the script has one scale across all 604.
SHEET_ASPECT = 1080 / 2160

CHROME_PATHS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def theme_css(palette: int, bg: str, fg: str, page: int) -> str:
    return (
        f"html,body{{background:{bg} !important;overflow:hidden !important;}}"
        f"#quran-page{{background:{bg} !important;}}"
        f".line-basmallah,.line-surah-name{{color:{fg} !important;}}"
        # Every font here is a COLR/CPAL colour font, so the colour comes from
        # the palette, not from CSS `color` - the basmala and the surah header
        # need the same palette as the ayah lines, or they stay black on dark.
        f'@font-palette-values --mp {{ font-family: "QPC_Page_{page}"; base-palette: {palette}; }}'
        f'@font-palette-values --mb {{ font-family: "Bismillah"; base-palette: {palette}; }}'
        f'@font-palette-values --mh {{ font-family: "SurahHeader"; base-palette: {palette}; }}'
        ".line-ayah { font-palette: --mp; }"
        ".line-basmallah { font-palette: --mb; }"
        ".line-surah-name { font-palette: --mh; }"
        # the engine's tap highlight must never end up in a picture
        ".word.highlighted{background:transparent !important;}"
    )


def parse_pages(spec: str) -> list[int]:
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages.update(range(int(a), int(b) + 1))
        elif part:
            pages.add(int(part))
    return sorted(p for p in pages if 1 <= p <= 604)


def serve(directory: Path) -> tuple[http.server.ThreadingHTTPServer, int]:
    class QuietHandler(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):  # no request log in the console
            pass

    handler = functools.partial(QuietHandler, directory=str(directory))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--engine", required=True, type=Path, help="KFGQPC_V4_layout folder (index.html, fonts/, script/)")
    ap.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent)
    ap.add_argument("--themes", nargs="+", default=["light", "dark"], choices=list(THEMES))
    ap.add_argument("--pages", default="1-604")
    ap.add_argument("--width", type=int, default=1080, help="output width in pixels")
    ap.add_argument("--skip-existing", action="store_true")
    args = ap.parse_args()

    engine: Path = args.engine.resolve()
    if not (engine / "index.html").exists():
        print(f"index.html not found in {engine}", file=sys.stderr)
        return 1

    pages = parse_pages(args.pages)
    width = args.width
    height = round(width / SHEET_ASPECT)
    # The engine sizes type in vw and caps it at 900 px, so the page is laid
    # out at 900 css px and scaled up to the requested width.
    css_w = 900
    css_h = round(css_w / SHEET_ASPECT)
    scale = width / css_w

    server, port = serve(engine)
    chrome = next((p for p in CHROME_PATHS if Path(p).exists()), None)
    t0 = time.time()
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=chrome, headless=True)
        ctx = browser.new_context(
            viewport={"width": css_w, "height": css_h},
            device_scale_factor=scale,
        )
        page = ctx.new_page()
        page.goto(f"http://127.0.0.1:{port}/index.html?page=1")
        page.wait_for_function("() => document.querySelector('#quran-page').children.length > 0")
        page.evaluate(
            "() => { const s = document.createElement('style'); s.id='__theme'; document.head.appendChild(s); }"
        )

        for theme in args.themes:
            palette, bg, fg = THEMES[theme]
            out_dir = args.out / theme
            out_dir.mkdir(parents=True, exist_ok=True)
            for n in pages:
                target = out_dir / f"p{n}.png"
                if args.skip_existing and target.exists() and target.stat().st_size > 5000:
                    continue
                page.evaluate(
                    """([n, css]) => {
                        loadPage(n);
                        document.getElementById('__theme').textContent = css;
                    }""",
                    [n, theme_css(palette, bg, fg, n)],
                )
                # wait until the page font (and header fonts) are really in
                page.evaluate(
                    """async (n) => {
                        await document.fonts.load("50px QPC_Page_" + n);
                        await document.fonts.load("50px SurahHeader");
                        await document.fonts.load("50px Bismillah");
                        await document.fonts.ready;
                        await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
                    }""",
                    n,
                )
                overflow = page.evaluate(
                    "() => document.getElementById('quran-page').scrollHeight - window.innerHeight"
                )
                if overflow > 0:
                    print(f"  ! p{n}: text is {overflow}px taller than the sheet", file=sys.stderr)
                png = page.screenshot(type="png")
                img = Image.open(io.BytesIO(png)).convert("RGB")
                if img.size != (width, height):
                    img = img.resize((width, height), Image.LANCZOS)
                # Adaptive palette keeps files small without visible loss.
                img.quantize(colors=256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE).save(
                    target, optimize=True
                )
                if n % 50 == 0 or n == pages[-1]:
                    print(f"{theme}: p{n}  ({time.time() - t0:.0f}s)", flush=True)
        browser.close()
    server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
