# turzx

Modular dashboard for the Turzx / Turing Smart Screen 5" (800×480, USB, "rev C" protocol).
Colors come from your Omarchy theme, and the screen follows the desktop session:
it dims during the screensaver and turns off when you lock.

![default layout](docs/default.png)

Design notes, protocol quirks and the reasoning behind the visual rules are in
[docs/DESIGN.md](docs/DESIGN.md).

## Setup

```sh
python3 -m venv .venv && .venv/bin/pip install -e .
sudo cp 70-turzx.rules /etc/udev/rules.d/ && sudo udevadm control --reload && sudo udevadm trigger --subsystem-match=tty
```

## Run

```sh
.venv/bin/turzx                          # drive the screen with layouts/default.toml
.venv/bin/turzx --layout compact         # use layouts/compact.toml
.venv/bin/turzx --list                   # list layout presets and widget types
.venv/bin/turzx --preview                # render to preview.png instead of the hardware (live)
.venv/bin/turzx --preview --once         # render one frame and exit
.venv/bin/turzx -c path/to/file.toml     # use any layout file
```

## Run at login

```sh
systemctl --user link "$PWD/contrib/turzx.service"
systemctl --user enable --now turzx
journalctl --user -u turzx -f            # logs
systemctl --user restart turzx           # after editing code or layouts
```

To make the service use another layout persistently:

```sh
systemctl --user edit turzx              # add:  [Service]
                                         #       Environment=TURZX_LAYOUT=compact
systemctl --user restart turzx
```

## Layouts

A layout is a TOML file in `layouts/`. It has a `[display]` table and a list of
`[[widget]]` entries. Each widget entry has a `type`, a `box = [x, y, width, height]`
on the 800×480 canvas, and any widget options.

To try a variant, copy `layouts/default.toml`, edit the copy, and check it with
`--preview --once` before running it on the screen.

Several widgets can share one card. Add a `[[card]]` entry with its own `box`,
then give each widget inside it `frame = false`. The default layout uses this
for the bottom row: AI limits on the left and agents on the right, in one card.

### `[display]` options

| key | default | meaning |
|---|---|---|
| `port` | `"auto"` | serial device path, or `"auto"` to find the awake screen |
| `brightness` | `50` | 0–100 while the desktop is active |
| `dim_brightness` | `10` | 0–100 while the Omarchy screensaver runs |
| `follow_session` | `true` | dim during the screensaver; turn off when locked or when all monitors are off |
| `flip` | `false` | rotate 180° if the screen is mounted upside down |
| `theme` | `"current"` | an Omarchy theme name, or `"current"` to follow the active theme live |

### Widgets

Every widget also accepts `interval`: the number of seconds between data refreshes. `color` options take a palette role name: `accent`, `cyan`, `magenta`, `green`, `yellow`, `orange` or `red`.

| type | options | shows |
|---|---|---|
| `clock_weather` | all `clock` + `weather` options | full-width card: time and date on the left; current weather and a 5-hour forecast on the right |
| `clock` | `format` (strftime, default `%-I:%M`), `ampm` | time, AM/PM, date |
| `weather` | `latitude`, `longitude`, `units` (`imperial`/`metric`) | current conditions + 5-hour forecast (Open-Meteo) |
| `cpu` | `label`, `color`, `temp_warn`, `temp_crit`, `power_warn`, `power_crit` | one-line load / temperature / package power over a history graph, per-core bars |
| `gpu` | `index`, `label`, `color`, `temp_warn`, `temp_crit`, `power_warn`, `power_crit` | one-line NVIDIA load / temperature / power over a history graph, VRAM bar |
| `ai_usage` | `providers` (`claude`, `codex`) | 5-hour / 7-day limits with pace markers |
| `memory` | `color` | one-line RAM % with a usage bar and GB used |
| `agents` | `active_minutes` (30), `working_seconds` (60), `rate_minutes` (5) | token burn (tok/min, today) and active Claude Code / Codex sessions |
| `network` | `interface` (default: all physical), `smooth` (samples, 3) | download/upload rate, never below the K unit (not in the default layout) |

## Writing a widget

Add `turzx/widgets/<name>.py`. It's registered automatically:

```python
from turzx import theme as t
from turzx.widget import Widget, register

@register("hello")
class Hello(Widget):
    interval = 5.0              # seconds between update() calls

    def update(self):           # runs on a worker thread; fetch data here
        self.msg = self.options.get("text", "hi")

    def draw(self, d, w, h):    # d is a PIL ImageDraw sized w×h
        y = t.card(d, w, h, "HELLO")
        d.text((t.PAD, y), self.msg, font=t.font(t.BODY), fill=t.TEXT)
```

Then add a `[[widget]]` entry with `type = "hello"` and a `box` to a layout.

Only pixels that changed since the last draw are sent to the screen.

Use the helpers in `turzx/theme.py` (`big_pct`, `pct_text`, `temp_text`,
`watts_text`, `stat_line`, `bar`, `sparkline`, `text_right`, `fields_right`, `icon`) so a new
widget follows the same visual rules as the others. The rules are described in
DESIGN.md.

## Tools

- `scripts/colortest.py` sends identical color swatches through the full-frame and
  partial-update encodings, to check the pixel format. Stop the service before running it.

## Code map

- `turzx/driver.py`: rev C serial protocol (`TurzxDisplay`) and `PreviewDisplay`
- `turzx/app.py`: CLI, layout loading, scheduler, dirty-region diffing, session and theme following
- `turzx/session.py`: lock, screensaver and monitor-power detection (Omarchy + Hyprland)
- `turzx/widget.py`: widget base class and registry
- `turzx/theme.py`: palette (from Omarchy), fonts, drawing helpers, number formatting
- `turzx/widgets/`: one module per widget
- `turzx/assets/`: Claude and Codex logos (rendered from Omarchy's SVGs)
- `layouts/`: layout presets
- `contrib/turzx.service`: systemd user unit
- `70-turzx.rules`: udev rule that gives the logged-in user access to the screen
