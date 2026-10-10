"""First/middle/last coverage for either sparse or dense chronological audits."""
import math


def covers_shot(times, duration):
    if type(duration) not in (int, float) or not math.isfinite(duration) or duration <= 0:
        return False
    if not isinstance(times, list) or len(times) < 3:
        return False
    if any(type(t) not in (int, float) or not math.isfinite(t) or not 0 <= t <= duration for t in times):
        return False
    if any(a >= b for a, b in zip(times, times[1:])):
        return False
    return (times[0] <= .25 and duration - times[-1] <= .3
            and min(abs(t - duration / 2) for t in times) <= .25)
