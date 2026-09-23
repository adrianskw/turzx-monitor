"""Widget base class and registry.

A widget owns one rectangle of the screen. The app calls `update()` every
`interval` seconds on a worker thread (so slow network/subprocess calls never
stall the screen), then calls `draw()` on the main thread with a fresh image
the size of the widget's box. Only pixels that actually changed get sent.

To add a widget: drop a module in turzx/widgets/, subclass Widget, decorate
with @register("name"), and reference it by name in layout.toml.
"""

from __future__ import annotations

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
        if "interval" in options:
            self.interval = float(options["interval"])

    def next_delay(self) -> float:
        """Seconds until the next update(); override to e.g. retry sooner after an error."""
        return self.interval

    def update(self) -> None:
        """Fetch data. Runs on a worker thread; store results on self."""

    def draw(self, d: ImageDraw.ImageDraw, w: int, h: int) -> None:
        raise NotImplementedError
