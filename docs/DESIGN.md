# Design notes

Why things are the way they are. Read this before changing the driver, the
render loop, or the visual rules.

## Hardware and protocol

The Turzx 5" is a Turing Smart Screen "rev C" device. The protocol comes from
[turing-smart-screen-python](https://github.com/mathoudebine/turing-smart-screen-python)
(`library/lcd/lcd_comm_rev_c.py`); `turzx/driver.py` is a minimal
reimplementation of the 5" subset.

**USB identities.** The screen shows up as two CDC-ACM devices on one internal
hub:

| id | serial | role |
|---|---|---|
| `1a86:ca21` | `CT21INCH` | helper/"sleeping" identity; opening it wakes the display |
| `1d6b:0106` | `20080411` | the display itself, the one we talk to |

The `CT21INCH` serial looks like a 2.1" screen, but it belongs to this 5" unit:
both identities re-enumerate together when the screen is replugged. The
`ttyACM` number of the display is not stable (it moved from 1 to 2 after a
self-reset), so the driver finds it by serial / vid:pid (`find_port`).

**Permissions.** `70-turzx.rules` tags the devices `uaccess`. The file must sort
*before* systemd's `73-seat-late.rules`, which is what turns the tag into an
ACL for the logged-in user. A `99-` prefix silently does nothing.

**Framing.** Every message is padded to a multiple of 250 bytes. Bulk pixel
payloads get a `0x00` inserted after every 249 bytes. Handshake reply:
`chs_5inch.dev1_rom1.88`, where `88` is the ROM version.

**Pixel formats.**
- Full frames: 800×480 BGRA, 4 bytes per pixel, landscape, row-major.
- Partial updates on ROM ≤ 88: 3-byte BGR. The reference code hints that ROM ≤ 88
  reads the low bits of B/G as alpha. `scripts/colortest.py` checked this: plain
  BGR, alpha-forced BGR and full frames rendered identically. 4-byte BGRA partials
  are *rejected* on ROM 88.

**Partial update layout.** Each row is sent as `3-byte (y*800 + x)`, then a
`2-byte width`, then the pixels. A header gives the total size and a counter.
After each update we send `QUERY_STATUS` and read a 1024-byte reply.

### The "needReSend" problem

Every 20–60 seconds, and often right after startup, the screen drops a partial
update. After that it answers `needReSend:1|renderCnt:0` to every status query
and **ignores all further partial updates** until it gets a full frame. The first
version froze because of this. The fix is in `TurzxDisplay.show_region`: any
status other than `needReSend:0…` sets `needs_full`, and the app then resends the
whole composed canvas (about 0.6 s). The root cause is unknown; the warnings in
the journal show how often it happens.

### Don't kill it mid-transfer

If the process dies halfway through a message, the screen can be left waiting
for bytes. It then either stalls (writes block even with CTS asserted) or resets
and re-enumerates. Consequences:
- Every write has a `write_timeout` (10 s), so a stall raises an error instead of
  hanging. The main loop reconnects on `OSError`.
- SIGTERM/SIGINT set `running = False`, so the loop exits between messages. On
  exit the backlight goes to 0, so a stopped dashboard doesn't show a frozen
  clock.
- If the screen still wedges, unplugging and replugging it recovers it.

## Render pipeline (`turzx/app.py`)

1. A layout gives a list of **slots**, each a widget plus a box.
2. `schedule()` runs each widget's `update()` on a thread pool when it is due.
   Slow sources (the usage CLIs, the weather HTTP call) therefore never block the
   screen. Due times align to the wall clock, so the clock flips exactly on the
   minute.
3. When an update finishes, that slot is marked dirty. `flush()` renders the
   widget into a fresh image of its box size and compares it with the previous
   image (`ImageChops.difference().getbbox()`). Only the changed rectangle is
   sent. A clock minute change is a few thousand pixels, not a full frame.
4. `repaint()` composes every widget into one canvas and sends a full frame. It
   runs at connect, after a resend request, after waking from "off", and on a
   theme change, so there's never a flash of blank cards.

Widgets draw with PIL only and never touch the device, which is why
`--preview` (PNG output) shows exactly what the screen will show.

## Session following (`turzx/session.py`)

This Omarchy version has no hypridle and no idle/lock hooks, so the app polls
every 2 s on the thread pool:

| check | how | effect |
|---|---|---|
| locked | `omarchy-shell lock isLocked` (not `-q`, which also suppresses the answer) | off |
| all monitors DPMS-off | `hyprctl monitors -j` | off |
| screensaver | a Hyprland client with class `org.omarchy.screensaver` | dim |

The first screensaver check used `pgrep -f`, which matched unrelated command
lines that happened to contain the class name. Matching the window class avoids
that.

"Off" means backlight 0 and no rendering. Waking triggers a full repaint.

The systemd user service is wanted by `graphical-session.target`. Under UWSM
that session environment already includes `HYPRLAND_INSTANCE_SIGNATURE`,
`WAYLAND_DISPLAY` and `OMARCHY_PATH`, so `hyprctl`, `omarchy-shell` and the usage
CLIs all work from the service.

## Theming

`theme.load()` maps roles to keys in an Omarchy `colors.toml`:

| role | Omarchy key | used for |
|---|---|---|
| BG | `darker_background` | gaps between cards |
| CARD | `background` | card fill |
| TEXT | `bright_foreground` | values |
| MUTED | `dark_foreground` | labels, titles |
| TRACK | `selection` | empty bar track |
| ACCENT | `accent` | CPU graph |
| GREEN / YELLOW / RED | `green` / `yellow` / `red` | level colors (<50 / 50–79 / ≥80 %) |
| ORANGE | `bright_yellow` | weather day icons |
| CYAN / MAGENTA | `bright_cyan` / `bright_magenta` | net down / up, GPU, night icons |

`theme = "current"` reads `~/.local/state/omarchy/current/theme/colors.toml`,
which includes user overlays, and watches its mtime to recolor live. The Claude
and Codex logos are brand colors, not theme colors.

## Visual rules

These come from iterating on the real panel. Keep them when adding widgets.

- **Fixed digit budgets.** Percentages and °C are capped at 99, CPU watts at 99,
  GPU watts at 999 (`capped`, `pct_text`, `temp_text`, `watts_text`). The labels
  are space-padded, and the font (JetBrains Mono Nerd Font) is monospace, so a
  new digit never shifts neighboring text. The weather temperature is °F and
  can pass 100, so it gets three digits and drops the unit.
- **Right-align anything that can change width.** Headline percentages
  (`big_pct` aligns to the width of "99%"), header readings (`fields_right`),
  the clock (time, AM/PM and date share a right edge), the weather temperature,
  RAM used/total, and network rates.
- **Nothing resizes with its data.** The VRAM bar's length is computed from the
  widest possible label (`999.9M/<total>`), not the current one.
- **Bars show the real value.** `bar()` clips an exact-width fill to the rounded
  track, instead of drawing a rounded fill that can't be narrower than its end
  caps. That was the original "low resolution" bug: a 12 px tall core bar
  couldn't show anything under about 45 %. Per-core CPU bars snap to 10 % steps
  (about 2.7 px each); VRAM, RAM and AI usage use 1 % steps (1.2–2 px each).
- **Squarish corners.** Cards use a 4 px radius and bars 3 px.
- **One grid.** Every row splits at x=400, and the bottom row also splits at 600,
  so the card edges line up down the screen. Row heights are 190 / 90 / 200.
  The top and bottom rows are single full-width cards. The middle row is
  CPU / GPU / RAM, sized 314 / 332 / 154 to fit their stat lines at 30 px:
  CPU needs 2 watt digits, GPU 3, and RAM only a percentage. Fitting three stat
  lines in 800 px is why the stat font went from 34 back to 30.
- **Use space for size, not decoration.** Headers only appear where the content
  isn't obvious. The AI card relies on logos, CPU and GPU are plain labels (model
  names were dropped), and weather has no condition text because the icon says
  it. CPU and GPU put usage %, temperature and watts on one `stat_line`, drawn
  over a faint history graph, with the core or VRAM bar underneath (no "VRAM"
  label). That keeps those cards 90 px tall, which leaves more room for the
  clock and weather. Graphs were dropped where they added
  nothing: RAM barely moves, so its graph was a flat shaded box, and the network
  graph was just noise.
- **Stable readouts.** Network rates are averaged over 3 samples and never drop
  below the K unit (idle reads `0.0K/s`), so idle chatter doesn't jump between
  B, K and M.
- **One fixed color per widget.** Bars and headline numbers don't change color
  with their value. Each widget has its own color: CPU accent blue, GPU magenta,
  RAM cyan, and Claude/Codex their brand colors. Load-based green/yellow/red was
  dropped because it made the RAM card yellow and the core bars green for no
  useful reason. Color changes are reserved for real warnings: temperature and
  power thresholds, and AI usage ahead of pace or at 80 % or more.
- **Bars fill their row.** Columns next to a bar are sized for their widest
  possible value ("99%", "6d23h"), and the bar takes all remaining width.
- **Warnings are per field.** Temperature and power turn yellow or red past
  per-widget thresholds. Defaults follow the hardware limits: the 7600X3D's
  Tjmax is 89 °C and its package power about 88 W; the 4070 SUPER throttles
  around 83 °C and has a 220 W board limit.

## Widget notes

- **cpu.** Package power comes from RAPL energy deltas at
  `/sys/class/powercap/intel-rapl:0/energy_uj`, which also exists on AMD and is
  world-readable here. Temperature is k10temp `Tctl`.
- **gpu.** NVML (`nvidia-ml-py`); in-process, cheaper than running `nvidia-smi`.
- **ai_usage.** Runs the same `claude-usage` / `codex-usage` CLIs as the Omarchy
  bar widgets, with `--format "{5h_pct}|{7d_pct}|{5h_reset}|{7d_reset}"` for
  output that's easy to parse. The **pace tick** marks how much of the window has
  elapsed, derived from the reset countdown. A percentage more than 5 points
  ahead of pace turns yellow, and ≥ 80 % turns red.
- **weather.** Open-Meteo, no key, one request for current, daily high/low and
  hourly data, every 10 min. It sometimes returns 503 "overloaded" or times out;
  `next_delay()` retries after 60 s on error. The hourly strip shows 5 hours,
  because 6 was too cramped at 420 px.
- **clock.** Updates every second so the minute flips on time, but the diffing
  means only a minute change actually sends pixels.
- **clock_weather.** Combines the two in one full-width card, because the clock
  alone left a lot of empty space. The left half is time and date
  (right-aligned to the center line). The right half has the icon,
  temperature and a high/low/humidity column on top, and 5 hourly columns
  below, each stacking hour, icon and temperature. Side-by-side icon and
  temperature was too cramped at 75 px per column. It
  reuses the `Clock` and `Weather` classes, and runs the weather fetch on its
  own thread so a slow HTTP call never delays the clock tick. The hourly list
  has no divider line; spacing separates it.

- **agents.** Reads the Claude Code and Codex session logs incrementally, only
  new bytes each time (about 15 MB/day on the first read), every 5 s. "Burn"
  counts fresh tokens: input + output + cache writes, with Codex input minus
  its cached input. Cache reads are excluded because they are over 95 % of raw
  input and cheap. A session is active if its log changed in the last 30 min,
  and working if it changed in the last 60 s. A long tool call can therefore
  briefly show as idle.
- **Shared cards.** `[[card]]` entries are drawn into the background image.
  Widgets with `frame = false` start from that background (`render` crops it)
  and draw inside `theme.frameless()`, where `card()` skips its own fill. It's
  how the AI limits and agents share one bottom card without a new composite
  widget.
- **network.** Removed from the default layout; the code is kept.

## Ideas not yet done

- Per-core bars could use 5 % steps (27 px wide bars allow it).
- A full-width strip freed by a denser layout could hold now-playing (MPRIS),
  the next calendar event, or disk I/O and NVMe temperature.
- Night brightness schedule.
