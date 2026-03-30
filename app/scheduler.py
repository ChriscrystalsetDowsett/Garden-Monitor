"""Camera schedule — automatically disables/enables the stream on a daily timer."""
import threading
import time
from datetime import datetime

from .config import SCHEDULE_ENABLED, SCHEDULE_OFF, SCHEDULE_ON


def _parse_hhmm(s):
    """Parse 'HH:MM' → (hour, minute) ints. Returns None on bad input."""
    try:
        h, m = s.strip().split(":")
        return int(h), int(m)
    except Exception:
        return None


def _camera_should_be_on():
    """Return True if the current local time falls within the on-window."""
    off_hm = _parse_hhmm(SCHEDULE_OFF)
    on_hm  = _parse_hhmm(SCHEDULE_ON)
    if off_hm is None or on_hm is None:
        return True   # bad config → don't disable anything

    now   = datetime.now()
    now_m = now.hour * 60 + now.minute
    off_m = off_hm[0] * 60 + off_hm[1]
    on_m  = on_hm[0]  * 60 + on_hm[1]

    if off_m > on_m:
        # Overnight window: off crosses midnight (e.g. 22:00 → 06:00)
        # Camera should be OFF when now >= off OR now < on
        return not (now_m >= off_m or now_m < on_m)
    else:
        # Same-day window: camera OFF between off_m and on_m
        return not (off_m <= now_m < on_m)


def _run(camera):
    """Background loop — checks the schedule every 30 s and updates camera.enabled."""
    while True:
        should_be_on = _camera_should_be_on()
        if camera.enabled != should_be_on:
            camera.set_enabled(should_be_on)
        time.sleep(30)


def start(camera):
    """Start the scheduler daemon thread. No-op if scheduling is disabled in config."""
    if not SCHEDULE_ENABLED:
        return

    # Apply the correct state immediately on startup before the thread loop begins
    camera.set_enabled(_camera_should_be_on())

    t = threading.Thread(target=_run, args=(camera,), daemon=True, name="scheduler")
    t.start()
