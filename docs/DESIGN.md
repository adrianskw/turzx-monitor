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
- **Use space for size, not decoration.** Headers only appear where the content
  isn't obvious. The AI card relies on logos, and the network card only has
  "NET". History graphs sit faintly behind headline numbers instead of taking
  their own rows. A RAM history graph was tried and dropped: RAM barely moves,
  so it drew as a flat shaded box.
- **Warnings are per field.** Temperature and power turn yellow or red past
  per-widget thresholds. Defaults follow the hardware limits: the 7600X3D's
  Tjmax is 89 °C and its package power about 88 W; the 4070 SUPER throttles
  around 83 °C and has a 220 W board limit.

## Widget notes

- **cpu.** Package power comes from RAPL energy deltas at
  `/sys/class/powercap/intel-rapl:0/energy_uj`, which also exists on AMD and is
  world-readable here. Temperature is k10temp `Tctl`. The name is taken from
  `/proc/cpuinfo` with vendor noise stripped.
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

## Ideas not yet done

- Per-core bars could use 5 % steps (27 px wide bars allow it).
- A full-width strip freed by a denser layout could hold now-playing (MPRIS),
  the next calendar event, or disk I/O and NVMe temperature.
- Night brightness schedule.
