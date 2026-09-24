"""Render what the screen should show to a PNG, without touching the hardware.

    .venv/bin/python scripts/preview.py                   # fixed sample data -> preview.png (~0.2 s)
    .venv/bin/python scripts/preview.py --live 6          # real data, collected for 6 s so graphs fill in
    .venv/bin/python scripts/preview.py -l compact -z 2   # another layout, 2x nearest-neighbour zoom
    .venv/bin/python scripts/preview.py --crop 0,190,800,90 -z 3   # zoom into one region
    .venv/bin/python scripts/preview.py --show            # also keep an imv window open on the PNG

--show opens an imv window on the output once; imv reloads the image whenever it
changes on disk, so every later render shows up in that same window.

Sample mode is deterministic (fixed clock, readings, weather), so it's the one to
compare before/after a layout or drawing change. Live mode runs the real widget
updates, including the weather request and the usage CLIs.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from turzx import app as turzx_app  # noqa: E402
from turzx import theme  # noqa: E402


def render(layout: Path, out: Path, live: float | None) -> None:
    cfg, slots, cards = turzx_app.load_config(layout, sample=live is None)
    theme.load(cfg.get("theme", "current"), cfg.get("muted_lift", 0.0))
    app = turzx_app.App(cfg, slots, str(out), cards)
    try:
        if live is None:
            app.run(once=True, sample=True)
            return
        app.connect()
        end = time.time() + live
        while time.time() < end:
            app.schedule(time.time())
            app.flush()
            time.sleep(0.05)
    finally:
        app.pool.shutdown(wait=False, cancel_futures=True)


def show(path: Path) -> None:
    """Open an imv window on path unless one is already open; imv reloads on change."""
    running = subprocess.run(["pgrep", "-f", f"imv.* {path}$"], capture_output=True, text=True).stdout.strip()
    if running:
        return
    subprocess.Popen(["imv", str(path)], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True, env=os.environ.copy())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-l", "--layout", default="default", help="preset name in layouts/ (default: default)")
    ap.add_argument("-c", "--config", type=Path, help="layout file path (overrides --layout)")
    ap.add_argument("-o", "--out", type=Path, default=ROOT / "preview.png", help="output PNG (default: preview.png)")
    ap.add_argument("--live", type=float, metavar="SECONDS", help="use real data, collected for SECONDS")
    ap.add_argument("-z", "--zoom", type=int, default=1, help="integer nearest-neighbour upscale for inspection")
    ap.add_argument("--crop", metavar="X,Y,W,H", help="crop to a region before zooming")
    ap.add_argument("--show", action="store_true", help="keep an imv window open on the output (auto-reloads)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    layout = args.config or turzx_app.LAYOUTS / f"{args.layout}.toml"
    if not layout.exists():
        raise SystemExit(f"layout not found: {layout}")
    if args.live is not None and args.live <= 0:
        raise SystemExit("--live needs a positive number of seconds")
    if args.zoom < 1:
        raise SystemExit("--zoom must be >= 1")

    started = time.time()
    out = args.out.resolve()
    # Render to a scratch file, then copy it over the output in one short write, so a
    # watching imv never reloads a half-written PNG.
    with tempfile.TemporaryDirectory() as tmp:
        scratch = Path(tmp) / "render.png"
        render(layout, scratch, args.live)
        if args.crop or args.zoom > 1:
            img = Image.open(scratch)
            if args.crop:
                x, y, w, h = (int(v) for v in args.crop.split(","))
                img = img.crop((x, y, x + w, y + h))
            if args.zoom > 1:
                img = img.resize((img.width * args.zoom, img.height * args.zoom), Image.NEAREST)
            img.save(scratch)
        shutil.copyfile(scratch, out)
    if args.show:
        show(out)
    mode = f"live {args.live:g}s" if args.live is not None else "sample data"
    print(f"{out} ({mode}, {layout.stem}, {time.time() - started:.1f}s)")


if __name__ == "__main__":
    main()
