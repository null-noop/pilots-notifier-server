"""Pure flight profile and phase calculations."""

import math


def analyze_flight_profile(trk: list[float], total_sec: float) -> tuple[dict, list[int]]:
    """Find climb/cruise/descent bounds and generate the five segment percentages."""
    points = []
    for index in range(0, len(trk), 4):
        if index + 3 < len(trk):
            points.append((trk[index], trk[index + 3]))

    if not points or total_sec <= 60.0:
        return {
            "t_takeoff": 30.0,
            "t_cruise_start": 30.0 + (total_sec - 60.0) * 0.25,
            "t_cruise_end": 30.0 + (total_sec - 60.0) * 0.75,
            "t_landed": max(30.0, total_sec - 30.0),
        }, [5, 22, 46, 22, 5]

    min_altitude = min(point[1] for point in points)
    max_altitude = max(point[1] for point in points)
    altitude_range = max_altitude - min_altitude
    cruise_threshold = min_altitude + (0.95 * altitude_range) if altitude_range > 50 else min_altitude

    cruise_start = next((point[0] for point in points if point[1] >= cruise_threshold), 30.0)
    cruise_end = next(
        (point[0] for point in reversed(points) if point[1] >= cruise_threshold),
        total_sec - 30.0,
    )
    cruise_start = max(30.0, min(cruise_start, total_sec - 30.0))
    cruise_end = max(cruise_start, min(cruise_end, total_sec - 30.0))

    ascent_duration = cruise_start - 30.0
    cruise_duration = cruise_end - cruise_start
    descent_duration = (total_sec - 30.0) - cruise_end
    middle_duration = max(1.0, total_sec - 60.0)

    ascent_pct = max(5.0, 90.0 * (ascent_duration / middle_duration))
    cruise_pct = max(10.0, 90.0 * (cruise_duration / middle_duration))
    descent_pct = max(5.0, 90.0 * (descent_duration / middle_duration))
    scale = 90.0 / (ascent_pct + cruise_pct + descent_pct)

    ascent_segment = max(5, int(round(ascent_pct * scale)))
    cruise_segment = max(10, int(round(cruise_pct * scale)))
    descent_segment = max(5, 90 - (ascent_segment + cruise_segment))
    bounds = {
        "t_takeoff": 30.0,
        "t_cruise_start": cruise_start,
        "t_cruise_end": cruise_end,
        "t_landed": max(30.0, total_sec - 30.0),
    }
    return bounds, [5, ascent_segment, cruise_segment, descent_segment, 5]


def get_stage_from_bounds(elapsed_sec: float, total_sec: float, bounds: dict) -> tuple[int, str]:
    """Map elapsed flight time to stages 1 through 5."""
    if elapsed_sec <= bounds["t_takeoff"]:
        return 1, "Takeoff"
    if elapsed_sec <= bounds["t_cruise_start"]:
        return 2, "Ascent"
    if elapsed_sec <= bounds["t_cruise_end"]:
        return 3, "Cruise"
    if elapsed_sec < bounds["t_landed"]:
        return 4, "Descent"
    return 5, "Landed"


def remaining_distance_nm(trk: list[float], time_sec: float) -> float | None:
    """Great-circle distance in nautical miles flown along the track from time_sec to its end."""
    points = [(trk[i], trk[i + 1], trk[i + 2]) for i in range(0, len(trk) - 3, 4)]
    if len(points) < 2:
        return None

    def haversine_nm(la1: float, lo1: float, la2: float, lo2: float) -> float:
        p1, p2 = math.radians(la1), math.radians(la2)
        a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lo2 - lo1) / 2) ** 2
        return 2 * 3440.065 * math.asin(math.sqrt(a))

    total = 0.0
    for (t1, la1, lo1), (t2, la2, lo2) in zip(points, points[1:]):
        if t2 <= time_sec:
            continue
        seg = haversine_nm(la1, lo1, la2, lo2)
        if t1 < time_sec and t2 > t1:
            seg *= (t2 - time_sec) / (t2 - t1)
        total += seg
    return total


def phase_start_distances(trk: list[float], bounds: dict) -> dict[str, int]:
    """Remaining nautical miles at the start of each phase, keyed by status code."""
    starts = {"1": 0.0, "2": bounds["t_takeoff"], "3": bounds["t_cruise_start"], "4": bounds["t_cruise_end"]}
    result = {}
    for status, start in starts.items():
        distance = remaining_distance_nm(trk, start)
        if distance is not None:
            result[status] = int(round(distance))
    return result


def describe_phase(status_code: int, flight: dict) -> str | None:
    """Build the notification description line for a phase from stored trip details."""
    base = _phase_detail(status_code, flight)
    remaining = (flight.get("phase_distances") or {}).get(str(status_code))
    if remaining is None or status_code == 5:
        return base
    return " · ".join(part for part in (base, f"{remaining:,} nm remaining") if part)


def _phase_detail(status_code: int, flight: dict) -> str | None:
    dep_runway = flight.get("dep_runway")
    arr_runway = flight.get("arr_runway")
    if status_code == 1 and dep_runway:
        return f"Departing RWY {dep_runway}"
    if status_code == 3 and flight.get("cruise_m"):
        flight_level = round(flight["cruise_m"] / 0.3048 / 1000) * 10
        return f"Cruising at FL{flight_level:03d}"
    if status_code == 4 and arr_runway:
        return f"Approaching RWY {arr_runway}"
    if status_code == 5:
        parts = []
        if arr_runway:
            parts.append(f"Landed on RWY {arr_runway}")
        if flight.get("pay"):
            parts.append(f"${flight['pay']:,} earned")
        return " · ".join(parts) or None
    return None


def find_photo_time(trk: list[float], lat: float, lon: float) -> float | None:
    """Return the track time (sec) of closest approach to a lat/lon, or None if the track is too short."""
    points = [(trk[i], trk[i + 1], trk[i + 2]) for i in range(0, len(trk) - 3, 4)]
    if len(points) < 2:
        return None

    cos_lat = math.cos(math.radians(lat))

    def xy(p_lat: float, p_lon: float) -> tuple[float, float]:
        return (p_lon - lon) * cos_lat, p_lat - lat

    best_dist = float("inf")
    best_time = None
    for (t1, la1, lo1), (t2, la2, lo2) in zip(points, points[1:]):
        x1, y1 = xy(la1, lo1)
        x2, y2 = xy(la2, lo2)
        dx, dy = x2 - x1, y2 - y1
        seg_len_sq = dx * dx + dy * dy
        frac = 0.0 if seg_len_sq == 0 else max(0.0, min(1.0, -(x1 * dx + y1 * dy) / seg_len_sq))
        dist = math.hypot(x1 + frac * dx, y1 + frac * dy)
        if dist < best_dist:
            best_dist = dist
            best_time = t1 + frac * (t2 - t1)
    return best_time


def time_to_bar_position(time_sec: float, total_sec: float, bounds: dict, segments: list[int]) -> int:
    """Map a flight time onto the 0-100 progress bar, which is divided by phase segments."""
    edges = [0.0, bounds["t_takeoff"], bounds["t_cruise_start"], bounds["t_cruise_end"], bounds["t_landed"], total_sec]
    start = 0
    for index, weight in enumerate(segments):
        lo, hi = edges[index], edges[index + 1]
        if time_sec <= hi or index == len(segments) - 1:
            frac = 0.0 if hi <= lo else max(0.0, min(1.0, (time_sec - lo) / (hi - lo)))
            return int(round(start + frac * weight))
        start += weight
    return 100
