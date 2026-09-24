"""Sun elevation from latitude/longitude and time, for day/night screen brightness.

A low-precision solar position (about 0.1° over this century, far more than a
brightness fade needs), computed locally so it works with no network.
"""

import math


def elevation(lat: float, lon: float, ts: float) -> float:
    """Degrees of the sun above the horizon (negative below) at unix time `ts`."""
    n = ts / 86400 + 2440587.5 - 2451545.0  # days since J2000.0
    mean_lon = (280.460 + 0.9856474 * n) % 360
    anomaly = math.radians((357.528 + 0.9856003 * n) % 360)
    ecl_lon = math.radians(mean_lon + 1.915 * math.sin(anomaly) + 0.020 * math.sin(2 * anomaly))
    obliquity = math.radians(23.439 - 0.0000004 * n)
    ra = math.atan2(math.cos(obliquity) * math.sin(ecl_lon), math.cos(ecl_lon))
    dec = math.asin(math.sin(obliquity) * math.sin(ecl_lon))
    sidereal = math.radians((280.46061837 + 360.98564736629 * n + lon) % 360)
    hour_angle = sidereal - ra
    lat = math.radians(lat)
    return math.degrees(math.asin(math.sin(lat) * math.sin(dec)
                                  + math.cos(lat) * math.cos(dec) * math.cos(hour_angle)))


def level(lat: float, lon: float, ts: float, day: int, night: int,
          low: float = -6.0, high: float = 6.0) -> int:
    """Brightness for this moment: `night` with the sun below `low` degrees (end of civil
    twilight), `day` above `high`, and a linear fade in between."""
    k = (elevation(lat, lon, ts) - low) / (high - low)
    return round(night + (day - night) * min(1.0, max(0.0, k)))
