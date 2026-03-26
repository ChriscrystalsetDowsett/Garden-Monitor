"""Shared configuration — loads settings.yaml, derives paths and constants."""
from pathlib import Path
import yaml

PROJECT_ROOT = Path(__file__).parent.parent   # garden-monitor/

# ── Load settings ─────────────────────────────────────────────────────────────
_cfg_path = PROJECT_ROOT / "config" / "settings.yaml"
with open(_cfg_path) as _f:
    _cfg = yaml.safe_load(_f)

# ── Server ────────────────────────────────────────────────────────────────────
SERVER_HOST = _cfg["server"]["host"]
SERVER_PORT = int(_cfg["server"]["port"])

# ── Data paths ────────────────────────────────────────────────────────────────
SNAPSHOT_DIR = (PROJECT_ROOT / _cfg["paths"]["photos"]).resolve()
VIDEOS_DIR   = (PROJECT_ROOT / _cfg["paths"]["videos"]).resolve()
SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
VIDEOS_DIR.mkdir(parents=True, exist_ok=True)

# ── Resolution table ──────────────────────────────────────────────────────────
RESOLUTIONS = {
    "640x480":   (640, 480),
    "1280x720":  (1280, 720),
    "1920x1080": (1920, 1080),
}

DEFAULT_RESOLUTION = _cfg["camera"]["default_resolution"]
if DEFAULT_RESOLUTION not in RESOLUTIONS:
    DEFAULT_RESOLUTION = "1280x720"

# ── libcamera constants ───────────────────────────────────────────────────────
AWB_MODES = {
    "auto": 0, "incandescent": 1, "tungsten": 2, "fluorescent": 3,
    "indoor": 4, "daylight": 5, "cloudy": 6,
}

NOISE_MODES = {"off": 0, "fast": 1, "high_quality": 2}

# ── Camera control defaults ───────────────────────────────────────────────────
# hflip/vflip seed from settings.yaml so the Pi can be mounted any way you like.
CAM_CTRL_DEFAULTS = {
    # Pre-capture: V4L2 hardware controls (C930e)
    "exposure_time":   0,       # 0 = auto (Aperture Priority); µs otherwise (V4L2 units ×100µs)
    "analogue_gain":   0.0,     # 0 = auto; otherwise maps to V4L2 gain 0–255
    "awb_mode":       "auto",   # "auto" | "manual"
    "awb_kelvin":      5600,    # colour temperature K (2000–7500), active when awb_mode=manual
    "brightness":      0,       # −100…+100 → V4L2 0–255 (neutral 128)
    "saturation":      0,       # −100…+100 → V4L2 0–255 (neutral 128)
    "sharpness":       1.0,     # 0–4 → V4L2 0–255 (neutral 128)
    "contrast":        1.0,     # 0–4 → V4L2 0–255 (neutral 128)
    "noise_reduction": "fast",  # kept for API compat; not applied on C930e/V4L2
    # Post-capture: OpenCV per-frame
    "tint":        0,           # −100 (green) … +100 (magenta)
    "hflip":       bool(_cfg["camera"].get("hflip", False)),
    "vflip":       bool(_cfg["camera"].get("vflip", False)),
    "film_filter": "none",
    "film_strength": 100,     # 0–100 %; blends filtered frame with original
}
