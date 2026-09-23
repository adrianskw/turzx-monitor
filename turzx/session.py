"""Desktop session state: lock, screensaver and monitor power (Omarchy/Hyprland)."""

from __future__ import annotations

import json
import subprocess

ON, DIM, OFF = "on", "dim", "off"


def _run(*cmd: str) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=3).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def locked() -> bool:
    return _run("omarchy-shell", "lock", "isLocked") == "true"  # -q would also suppress the answer


def _hyprctl(what: str) -> list:
    try:
        return json.loads(_run("hyprctl", what, "-j") or "[]")
    except json.JSONDecodeError:
        return []


def screensaver() -> bool:
    """Omarchy's screensaver is a terminal window with this class."""
    return any(c.get("class") == "org.omarchy.screensaver" for c in _hyprctl("clients"))


def monitors_off() -> bool:
    mons = _hyprctl("monitors")
    return bool(mons) and not any(m.get("dpmsStatus", True) for m in mons)


def mode() -> str:
    if locked() or monitors_off():
        return OFF
    if screensaver():
        return DIM
    return ON
