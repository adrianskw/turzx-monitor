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

from turzx import session, sun, theme
from turzx.driver import HEIGHT, WIDTH, PreviewDisplay, TurzxDisplay
from turzx.widget import REGISTRY, Widget
import turzx.widgets  # noqa: F401  (registers widgets)
from turzx.widgets.weather import coordinates

log = logging.getLogger("turzx")
LAYOUTS = Path(__file__).resolve().parent.parent / "layouts"


@dataclass
class Slot:
    widget: Widget
    box: tuple[int, int, int, int]  # x, y, w, h
    frame: bool = True  # False: draw inside a shared [[card]] instead of its own
    next_due: float = 0.0
    future: Future | None = None
    ready: bool = False
    dirty: bool = True
    last: Image.Image | None = field(default=None, repr=False)
    next_frame: float = 0.0


def _box(value: object, label: str) -> tuple[int, int, int, int]:
    if not isinstance(value, list) or len(value) != 4 or any(type(n) is not int for n in value):
        raise SystemExit(f"{label}: box must be [x, y, width, height] with integers")
    x, y, w, h = value
    if x < 0 or y < 0 or w <= 0 or h <= 0 or x + w > WIDTH or y + h > HEIGHT:
        raise SystemExit(f"{label}: box {value} must fit inside the {WIDTH}x{HEIGHT} display")
    return x, y, w, h


def _overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _contains(outer: tuple[int, int, int, int], inner: tuple[int, int, int, int]) -> bool:
    ox, oy, ow, oh = outer
    x, y, w, h = inner
    return ox <= x and oy <= y and x + w <= ox + ow and y + h <= oy + oh


def _env_location() -> dict:
    """Location from $TURZX_LATITUDE / $TURZX_LONGITUDE, so it can stay out of the layouts
    (e.g. in a systemd drop-in). Empty unless both are set."""
    values = {name: os.environ[env] for name, env in
              (("latitude", "TURZX_LATITUDE"), ("longitude", "TURZX_LONGITUDE")) if env in os.environ}
    if not values:
        return {}
    try:
        lat, lon = coordinates(values)
    except ValueError as exc:
        raise SystemExit(f"TURZX_LATITUDE / TURZX_LONGITUDE: {exc}") from exc
    return {"latitude": lat, "longitude": lon}


def load_config(path: Path, sample: bool = False) -> tuple[dict, list[Slot], list[tuple[int, int, int, int]]]:
    cfg = tomllib.loads(path.read_text())
    home = _env_location()  # fills in where the layout gives no location
    cards = [_box(c.get("box"), f"card[{i}]") for i, c in enumerate(cfg.get("card", []), 1)]
    for i, card in enumerate(cards):
        for j, other in enumerate(cards[:i], 1):
            if _overlap(card, other):
                raise SystemExit(f"card[{i + 1}] overlaps card[{j}]")
    slots = []
    for i, spec in enumerate(cfg.get("widget", []), 1):
        spec = dict(spec)
        kind = spec.pop("type", None)
        if kind in ("weather", "clock_weather", "forecast"):  # time-only cards use it for sun times
            spec = {**home, **spec}
        label = f"widget[{i}] ({kind or 'missing type'})"
        box = _box(spec.pop("box", None), label)
        frame = spec.pop("frame", True)
        if type(frame) is not bool:
            raise SystemExit(f"{label}: frame must be true or false")
        if not isinstance(kind, str) or kind not in REGISTRY:
            raise SystemExit(f"unknown widget type {kind!r}; available: {', '.join(sorted(REGISTRY))}")
        for j, other in enumerate(slots, 1):
            if _overlap(box, other.box):
                raise SystemExit(f"{label} overlaps widget[{j}] ({other.widget.kind})")
        if frame and any(_overlap(box, card) for card in cards):
            raise SystemExit(f"{label}: framed widget overlaps a shared card")
        if not frame and not any(_contains(card, box) for card in cards):
            raise SystemExit(f"{label}: frame=false requires a containing [[card]]")
        try:
            widget = REGISTRY[kind](**spec, _sample=True) if sample and kind in ("cpu", "gpu") else REGISTRY[kind](**spec)
        except ValueError as exc:
            raise SystemExit(f"{label}: {exc}") from exc
        slots.append(Slot(widget, box, frame))
    sources = {slot.widget.kind: slot.widget for slot in slots if slot.widget.kind in ("cpu", "gpu")}
    for slot in slots:
        if slot.widget.kind == "agents":
            slot.widget.bind_history_sources(sources)
        elif slot.widget.kind == "clock_weather" and slot.widget.options.get("align") == "time":
            row = slot.box[1], slot.box[3]
            partner = next((other for other in slots if other.widget.kind == "clock_weather"
                            and other.widget.options.get("show") == "time"
                            and (other.box[1], other.box[3]) == row), None)
            if partner is not None:
                slot.widget.align_with_time(partner.widget, partner.box[2], partner.box[3])
    return {**home, **cfg.get("display", {})}, slots, cards


def background(size: tuple[int, int], cards: list[tuple]) -> Image.Image:
    """Screen background: gap color plus the shared cards that frameless widgets sit in."""
    img = Image.new("RGB", size, theme.BG)
    d = theme.Draw(img)
    for box in cards:
        theme.card_rect(d, *box)
    return img


def render(slot: Slot, bg: Image.Image | None = None) -> Image.Image:
    x, y, w, h = slot.box
    if slot.frame:
        img = Image.new("RGB", (w, h), theme.BG)
    else:  # start from the shared card underneath
        img = bg.crop((x, y, x + w, y + h)) if bg else Image.new("RGB", (w, h), theme.CARD)
    d = theme.Draw(img)
    if not slot.frame:
        with theme.frameless():
            _draw(slot, d, w, h)
    else:
        _draw(slot, d, w, h)
    return img


def _draw(slot: Slot, d: ImageDraw.ImageDraw, w: int, h: int) -> None:
    if not slot.ready:
        theme.card(d, w, h, slot.widget.kind.upper())
        return
    try:
        slot.widget.draw(d, w, h)
    except Exception:
        log.exception("%s.draw failed", slot.widget.kind)


def _run_update(widget: Widget, once: bool = False) -> None:
    try:
        if once:
            widget.update_once()
        else:
            widget.update()
    except Exception:
        log.exception("%s.update failed", widget.kind)


def _location(display_cfg: dict, slots) -> tuple[float, float] | None:
    """(latitude, longitude) from [display], else from the first widget that has them."""
    for opts in [display_cfg] + [s.widget.options for s in slots]:
        try:
            location = coordinates(opts)
        except ValueError as exc:
            raise SystemExit(f"invalid location: {exc}") from exc
        if location is not None:
            return location
    return None


class App:
    def __init__(self, display_cfg: dict, slots: list[Slot], preview: str | None, cards: list[tuple] = ()):
        self.cfg = display_cfg
        self.slots = slots
        self.cards = list(cards)
        self.preview = preview
        self.pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="widget")
        self.display = None
        self.day_brightness = self.brightness = int(display_cfg.get("brightness", 50))
        self.dim_brightness = int(display_cfg.get("dim_brightness", 10))
        # day/night: fade `brightness` down to `night_brightness` through twilight, at the
        # layout's location ([display] latitude/longitude, else the weather widget's)
        self.night_brightness = display_cfg.get("night_brightness")
        if self.night_brightness is not None and not 0 <= int(self.night_brightness) <= 100:
            raise SystemExit("night_brightness must be 0-100")
        self.location = _location(display_cfg, slots) if self.night_brightness is not None else None
        if self.night_brightness is not None and self.location is None:
            log.warning("night_brightness needs latitude/longitude in [display] or a weather widget; ignoring it")
        self._next_sun_check = 0.0
        self._update_sun(time.time())
        self.follow_session = bool(display_cfg.get("follow_session", True)) and preview is None
        self.theme_name = display_cfg.get("theme", "current")
        self.mode = session.ON
        self.running = True
        self._mode_future: Future | None = None
        self._next_session_check = 0.0
        self._last_wall: float | None = None
        self._theme_mtime = self._theme_stamp()

    def _check_clock(self, now: float) -> None:
        """A backward wall-clock correction must not defer updates for its duration."""
        if self._last_wall is not None and now < self._last_wall - 1:
            log.info("wall clock moved backward; rescheduling widgets")
            self._next_session_check = self._next_sun_check = 0.0
            for slot in self.slots:
                slot.next_due = slot.next_frame = 0.0
        self._last_wall = now

    def connect(self):
        if self.preview is not None:
            self.display = PreviewDisplay(self.preview)
        else:
            self.display = TurzxDisplay(
                port=self.cfg.get("port", "auto"),
                brightness=self.brightness,
                flip=bool(self.cfg.get("flip", False)),
            )
        self.mode = session.ON
        self.repaint()

    def repaint(self) -> None:
        """Compose every widget into one full frame (no flash of blank cards)."""
        self.bg = background(self.display.size, self.cards)
        self.canvas = self.bg.copy()
        for s in self.slots:
            s.last = render(s, self.bg)
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
        self._check_clock(now)
        if self._update_sun(now) and self.mode == session.ON and self.display is not None:
            log.info("brightness %d (sun)", self.brightness)
            self.display.set_brightness(self.brightness)
        if now < self._next_session_check:
            return
        if self._mode_future is None:
            if self.follow_session:
                self._mode_future = self.pool.submit(session.mode)
            stamp = self._theme_stamp()
            if stamp != self._theme_mtime:
                log.info("theme changed; reloading %s", self.theme_name)
                try:
                    theme.load(self.theme_name)
                except (OSError, ValueError, SystemExit) as exc:
                    log.warning("theme reload failed; retaining current colors: %s", exc)
                else:
                    self._theme_mtime = stamp
                    if self.mode != session.OFF:
                        self.repaint()
        if self._mode_future is not None and self._mode_future.done():
            try:
                new = self._mode_future.result()
            except Exception:
                log.exception("session state check failed; retrying")
                new = self.mode
            self._mode_future = None
            self._next_session_check = now + 2
            if new != self.mode:
                self._apply_mode(new)
        elif self._mode_future is None:
            self._next_session_check = now + 2

    def _update_sun(self, now: float) -> bool:
        """Recompute the day/night brightness once a minute; True if it changed."""
        if self.location is None or now < self._next_sun_check:
            return False
        self._next_sun_check = now + 60
        level = sun.level(*self.location, now, self.day_brightness, int(self.night_brightness))
        if level == self.brightness:
            return False
        self.brightness = level
        return True

    def _apply_mode(self, new: str) -> None:
        log.info("session %s -> %s", self.mode, new)
        was_off, self.mode = self.mode == session.OFF, new
        level = {session.ON: self.brightness, session.DIM: self.dim_brightness, session.OFF: 0}[new]
        self.display.set_brightness(level)
        if was_off and new != session.OFF:
            self.repaint()  # frames were skipped while off

    def schedule(self, now: float) -> None:
        self._check_clock(now)
        for s in self.slots:
            if s.future is None and now >= s.next_due:
                s.future = self.pool.submit(_run_update, s.widget)
            if s.future is not None and s.future.done():
                s.future, s.ready, s.dirty = None, True, True
                # align to wall clock so e.g. the clock ticks right on the second
                iv = s.widget.next_delay()
                if not isinstance(iv, (int, float)) or not math.isfinite(iv) or iv <= 0:
                    log.error("%s.next_delay() returned %r; using interval %s", s.widget.kind, iv, s.widget.interval)
                    iv = s.widget.interval
                s.next_due = math.floor(now / iv + 1) * iv
                s.next_frame = 0.0  # new data may need a faster animation cadence
            fi = s.widget.frame_interval
            if fi and s.ready and now >= s.next_frame:  # animation: redraw, don't re-fetch
                s.dirty = True
                s.next_frame = s.widget.next_frame(now)

    def flush(self) -> None:
        for s in self.slots:
            if not s.dirty:
                continue
            s.dirty = False
            x, y, _, _ = s.box
            img = render(s, self.bg)
            bbox = (0, 0, *img.size) if s.last is None else ImageChops.difference(img, s.last).getbbox()
            s.last = img
            if bbox:
                region = img.crop(bbox)
                self.canvas.paste(region, (x + bbox[0], y + bbox[1]))
                self.display.show_region(region, x + bbox[0], y + bbox[1])
        if getattr(self.display, "needs_full", False):
            self.display.show_full(self.canvas)

    def run(self, once: bool = False, sample: bool = False) -> None:
        try:
            self.connect()
        except OSError:
            if self.preview is not None:
                raise  # a PNG path error cannot be fixed by reconnecting hardware
            log.exception("display init failed; retrying")
            self._reconnect()
        if not self.running:
            return
        if sample:
            from turzx.sample import populate

            for slot in self.slots:
                populate(slot.widget)
                slot.ready = slot.dirty = True
            self.flush()
            return
        if once:
            futures = {self.pool.submit(_run_update, s.widget, True): s for s in self.slots}
            done, pending = wait(futures, timeout=60)
            if pending:
                log.warning("%d widget update(s) did not finish within 60 seconds", len(pending))
            for future in done:
                slot = futures[future]
                slot.ready = True
                slot.dirty = True
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
                if self.preview is not None:
                    raise
                log.exception("display I/O error; reconnecting")
                self._reconnect()
            time.sleep(0.05)

    def _reconnect(self) -> None:
        if self.display is not None:
            try:
                self.display.close()
            except Exception:
                pass
            self.display = None
        while self.running:
            time.sleep(3)
            if not self.running:
                break
            try:
                self.connect()
                return
            except Exception as e:
                log.warning("reconnect failed: %s", e)
                if self.display is not None:
                    try:
                        self.display.close()
                    except Exception:
                        pass
                    self.display = None


def main() -> None:
    ap = argparse.ArgumentParser(description="Modular dashboard for the Turzx 5\" display")
    ap.add_argument("-l", "--layout", default=os.environ.get("TURZX_LAYOUT", "default"),
                    help="layout preset name from layouts/ (default: $TURZX_LAYOUT or 'default')")
    ap.add_argument("-c", "--config", type=Path, help="path to a layout file (overrides --layout)")
    ap.add_argument("--list", action="store_true", help="list layout presets and widget types, then exit")
    ap.add_argument("--preview", nargs="?", const="preview.png", metavar="PNG",
                    help="render to a PNG instead of the hardware")
    ap.add_argument("--once", action="store_true", help="render a single frame and exit")
    ap.add_argument("--sample-data", action="store_true", help="use fixed data for --preview --once")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    if args.sample_data and (not args.preview or not args.once):
        ap.error("--sample-data requires --preview and --once")
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
    display_cfg, slots, cards = load_config(path, sample=args.sample_data)
    theme.load(display_cfg.get("theme", "current"), display_cfg.get("muted_lift", 0.0))
    theme.use_font(display_cfg.get("font"), display_cfg.get("font_scale", "match"))
    theme.set_margin(display_cfg.get("margin"))
    app = App(display_cfg, slots, args.preview, cards)

    def stop(*_):  # finish the in-flight update instead of dying mid-transfer
        app.running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        app.run(once=args.once, sample=args.sample_data)
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
