# turzx: notes for Claude

Read `README.md` for usage and `docs/DESIGN.md` for protocol quirks and the visual rules (digit caps, right-alignment, exact-width bars) before changing anything.

- Render with `scripts/preview.py --show`: the user watches a live `imv` window on `preview.png` (the Claude CLI can't show images inline). Render the image they should see last.
- Iterate on layouts with `.venv/bin/turzx -l <name> --preview <png> --once` and look at the PNG; `--once` shows no graphs or CPU power (it takes a single sample).
- The screen is driven by the `turzx` systemd user service. Apply changes with `systemctl --user restart turzx`; logs are in `journalctl --user -u turzx`.
- Stop a manual run with `pkill -x turzx`. `pkill -f` matches the invoking shell's own command line and kills it.
- Don't kill a hardware run mid-transfer (e.g. `timeout` on a live run): the screen can wedge or reset. If it stops answering HELLO, it may need a replug.
- The "screen dropped an update … resend full frame" warnings are expected; see DESIGN.md.
