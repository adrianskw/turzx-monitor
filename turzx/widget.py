"""Widget base class and registry.

A widget owns one rectangle of the screen. The app calls `update()` every
`interval` seconds on a worker thread (so slow network/subprocess calls never
stall the screen), then calls `draw()` on the main thread with a fresh image
the size of the widget's box. Only pixels that actually changed get sent.

To add a widget: drop a module in turzx/widgets/, subclass Widget, decorate
with @register("name"), and reference it by name in layout.toml.
"""

from __future__ import annotations

import math
from typing import Any

from PIL import ImageDraw

REGISTRY: dict[str, type["Widget"]] = {}


def register(name: str):
    def deco(cls):
        REGISTRY[name] = cls
        cls.kind = name
        return cls
    return deco


class Widget:
    kind = "widget"
    interval: float = 1.0  # seconds between update() calls

    def __init__(self, **options: Any):
        self.options = options
        value = options.get("interval", self.interval)
        if isinstance(value, bool):
            raise ValueError("interval must be a positive finite number")
        try:
            self.interval = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("interval must be a positive finite number") from exc
        if not math.isfinite(self.interval) or self.interval <= 0:
            raise ValueError("interval must be a positive finite number")

    frame_interval: float | None = None  # seconds between redraws for animation (None = only after update)

    def next_frame(self, now: float) -> float:
        """Wall-clock time of the next animation redraw; override to slow down when idle."""
        return math.floor(now / self.frame_interval + 1) * self.frame_interval

    def next_delay(self) -> float:
        """Seconds until the next update(); override to e.g. retry sooner after an error."""
        return self.interval

    def update(self) -> None:
        """Fetch data. Runs on a worker thread; store results on self."""

    def update_once(self) -> None:
        """Fetch data for a one-shot render; composite widgets may wait for their sources."""
        self.update()

    def draw(self, d: ImageDraw.ImageDraw, w: int, h: int) -> None:
        raise NotImplementedError
