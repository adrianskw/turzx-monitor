"""Main loop: schedule widget updates, redraw what changed, push dirty pixels."""

from __future__ import annotations

import argparse
import logging
import math
import os
import signal
import time
import tomllib
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from turzx import session, theme
from turzx.driver import PreviewDisplay, TurzxDisplay
from turzx.widget import REGISTRY, Widget
import turzx.widgets  # noqa: F401  (registers widgets)

log = logging.getLogger("turzx")
LAYOUTS = Path(__file__).resolve().parent.parent / "layouts"


@dataclass
class Slot:
    widget: Widget
    box: tuple[int, int, int, int]  # x, y, w, h
    next_due: float = 0.0
    future: Future | None = None
    ready: bool = False
    dirty: bool = True
    last: Image.Image | None = field(default=None, repr=False)


def load_config(path: Path) -> tuple[dict, list[Slot]]:
    cfg = tomllib.loads(path.read_text())
    slots = []
    for spec in cfg.get("widget", []):
        spec = dict(spec)
        kind, box = spec.pop("type"), tuple(spec.pop("box"))
        if kind not in REGISTRY:
            raise SystemExit(f"unknown widget type {kind!r}; available: {', '.join(sorted(REGISTRY))}")
        slots.append(Slot(REGISTRY[kind](**spec), box))
    return cfg.get("display", {}), slots


def render(slot: Slot) -> Image.Image:
    _, _, w, h = slot.box
    img = Image.new("RGB", (w, h), theme.BG)
    d = ImageDraw.Draw(img)
    if not slot.ready:
        theme.card(d, w, h, slot.widget.kind.upper())
        return img
    try:
        slot.widget.draw(d, w, h)
    except Exception:
        log.exception("%s.draw failed", slot.widget.kind)
    return img


def _run_update(widget: Widget) -> None:
    try:
        widget.update()
    except Exception:
        log.exception("%s.update failed", widget.kind)


class App:
    def __init__(self, display_cfg: dict, slots: list[Slot], preview: str | None):
        self.cfg = display_cfg
        self.slots = slots
        self.preview = preview
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="widget")
        self.display = None
        self.brightness = int(display_cfg.get("brightness", 50))
        self.dim_brightness = int(display_cfg.get("dim_brightness", 10))
        self.follow_session = bool(display_cfg.get("follow_session", True)) and not preview
        self.theme_name = display_cfg.get("theme", "current")
        self.mode = session.ON
        self.running = True
        self._mode_future: Future | None = None
        self._next_session_check = 0.0
        self._theme_mtime = self._theme_stamp()

    def connect(self):
        if self.preview:
            self.display = PreviewDisplay(self.preview)
        else:
            self.display = TurzxDisplay(
                port=self.cfg.get("port", "auto"),
                brightness=int(self.cfg.get("brightness", 50)),
                flip=bool(self.cfg.get("flip", False)),
            )
        self.mode = session.ON
        self.repaint()

    def repaint(self) -> None:
        """Compose every widget into one full frame (no flash of blank cards)."""
        self.canvas = Image.new("RGB", self.display.size, theme.BG)
        for s in self.slots:
            s.last = render(s)
            s.dirty = False
            self.canvas.paste(s.last, s.box[:2])
        self.display.show_full(self.canvas)

    def _theme_stamp(self) -> float | None:
        try:
            return theme.theme_path(self.theme_name).stat().st_mtime
        except (OSError, SystemExit):
            return None

    def watch_session(self, now: float) -> None:
        """Every couple of seconds: follow lock/screensaver state and theme changes."""
        if now < self._next_session_check:
            return
        if self._mode_future is None:
            if self.follow_session:
                self._mode_future = self.pool.submit(session.mode)
            stamp = self._theme_stamp()
            if stamp != self._theme_mtime:
                self._theme_mtime = stamp
                log.info("theme changed; reloading %s", self.theme_name)
                theme.load(self.theme_name)
                if self.mode != session.OFF:
                    self.repaint()
        if self._mode_future is not None and self._mode_future.done():
            new = self._mode_future.result()
            self._mode_future = None
            self._next_session_check = now + 2
            if new != self.mode:
                self._apply_mode(new)
        elif self._mode_future is None:
            self._next_session_check = now + 2

    def _apply_mode(self, new: str) -> None:
        log.info("session %s -> %s", self.mode, new)
        was_off, self.mode = self.mode == session.OFF, new
        level = {session.ON: self.brightness, session.DIM: self.dim_brightness, session.OFF: 0}[new]
        self.display.set_brightness(level)
        if was_off and new != session.OFF:
            self.repaint()  # frames were skipped while off

    def schedule(self, now: float) -> None:
        for s in self.slots:
            if s.future is None and now >= s.next_due:
                s.future = self.pool.submit(_run_update, s.widget)
            if s.future is not None and s.future.done():
                s.future, s.ready, s.dirty = None, True, True
                # align to wall clock so e.g. the clock ticks right on the second
                iv = s.widget.next_delay()
                s.next_due = math.floor(now / iv + 1) * iv

    def flush(self) -> None:
        for s in self.slots:
            if not s.dirty:
                continue
            s.dirty = False
            x, y, _, _ = s.box
            img = render(s)
            bbox = (0, 0, *img.size) if s.last is None else ImageChops.difference(img, s.last).getbbox()
            s.last = img
            if bbox:
                region = img.crop(bbox)
                self.canvas.paste(region, (x + bbox[0], y + bbox[1]))
                self.display.show_region(region, x + bbox[0], y + bbox[1])
        if getattr(self.display, "needs_full", False):
            self.display.show_full(self.canvas)

    def run(self, once: bool = False) -> None:
        try:
            self.connect()
        except OSError:
            log.exception("display init failed; retrying")
            self._reconnect()
        if once:
            wait([self.pool.submit(_run_update, s.widget) for s in self.slots], timeout=60)
            for s in self.slots:
                s.ready = True
            self.flush()
            return
        while self.running:
            try:
                now = time.time()
                self.watch_session(now)
                if self.mode != session.OFF:
                    self.schedule(now)
                    self.flush()
            except OSError:  # serial link dropped (unplug, screen reset, suspend)
                log.exception("display I/O error; reconnecting")
                self._reconnect()
            time.sleep(0.05)

    def _reconnect(self) -> None:
        try:
            self.display.close()
        except Exception:
            pass
        while True:
            time.sleep(3)
            try:
                self.connect()
                return
            except Exception as e:
                log.warning("reconnect failed: %s", e)
                try:
                    self.display.close()
                except Exception:
                    pass


def main() -> None:
    ap = argparse.ArgumentParser(description="Modular dashboard for the Turzx 5\" display")
    ap.add_argument("-l", "--layout", default=os.environ.get("TURZX_LAYOUT", "default"),
                    help="layout preset name from layouts/ (default: $TURZX_LAYOUT or 'default')")
    ap.add_argument("-c", "--config", type=Path, help="path to a layout file (overrides --layout)")
    ap.add_argument("--list", action="store_true", help="list layout presets and widget types, then exit")
    ap.add_argument("--preview", nargs="?", const="preview.png", metavar="PNG",
                    help="render to a PNG instead of the hardware")
    ap.add_argument("--once", action="store_true", help="render a single frame and exit")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list:
        print("layouts:", ", ".join(sorted(p.stem for p in LAYOUTS.glob("*.toml"))))
        print("widgets:", ", ".join(sorted(REGISTRY)))
        return
    path = args.config or LAYOUTS / f"{args.layout}.toml"
    if not path.exists():
        raise SystemExit(f"layout not found: {path} (try --list)")
    log.info("layout %s", path)
    display_cfg, slots = load_config(path)
    theme.load(display_cfg.get("theme", "current"))
    app = App(display_cfg, slots, args.preview)

    def stop(*_):  # finish the in-flight update instead of dying mid-transfer
        app.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app.run(once=args.once)
    finally:
        app.pool.shutdown(wait=False, cancel_futures=True)
        if app.display:
            try:
                if not args.once:
                    app.display.set_brightness(0)  # don't leave a frozen dashboard lit
                app.display.close()
            except OSError:
                pass


if __name__ == "__main__":
    main()
