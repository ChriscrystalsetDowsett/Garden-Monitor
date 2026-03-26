"""Camera hardware management, OpenCV post-processing pipeline, and stream output."""
import io, time, threading, subprocess
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

from .config import SNAPSHOT_DIR, RESOLUTIONS, CAM_CTRL_DEFAULTS, DEFAULT_RESOLUTION
from .film import FILM_FILTERS
from .postprocess import postprocess_jpeg

DEVICE       = "/dev/video0"
FPS          = 24
JPEG_QUALITY = 85

# ── Live camera controls (OpenCV per-frame effects) ────────────────────────────
# Shared mutable state; protected by cam_ctrl_lock.
cam_ctrl = dict(CAM_CTRL_DEFAULTS)
cam_ctrl_lock = threading.Lock()


# ── OpenCV post-processing ─────────────────────────────────────────────────────
def _apply_ocv(buf, s):
    """Post-processing: tint shift, flip, film simulation.
    Brightness/contrast/saturation/sharpness are handled by V4L2 hardware."""
    try:
        arr   = np.frombuffer(buf, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return buf

        # Flip
        hf, vf = s.get("hflip", False), s.get("vflip", False)
        if hf and vf:   frame = cv2.flip(frame, -1)
        elif hf:        frame = cv2.flip(frame, 1)
        elif vf:        frame = cv2.flip(frame, 0)

        # Tint: green (−) or magenta (+) channel shift
        t = s.get("tint", 0)
        if t:
            strength = abs(t) * 40 // 100
            ch = list(cv2.split(frame.astype(np.int16)))
            if t > 0:   # magenta: boost R+B, reduce G
                ch[2] = np.clip(ch[2] + strength, 0, 255)
                ch[0] = np.clip(ch[0] + strength // 2, 0, 255)
                ch[1] = np.clip(ch[1] - strength, 0, 255)
            else:       # green: boost G, reduce R+B
                ch[1] = np.clip(ch[1] + strength, 0, 255)
                ch[2] = np.clip(ch[2] - strength, 0, 255)
                ch[0] = np.clip(ch[0] - strength // 2, 0, 255)
            frame = cv2.merge([c.astype(np.uint8) for c in ch])

        # Film simulation
        ff = s.get("film_filter", "none")
        if ff and ff != "none":
            fd = FILM_FILTERS.get(ff)
            if fd:
                original = frame.copy()
                if fd.get("bw"):
                    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                    gray = fd["curve"][gray]
                    frame = cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)
                else:
                    b_ch, g_ch, r_ch = cv2.split(frame)
                    frame = cv2.merge([fd["b"][b_ch], fd["g"][g_ch], fd["r"][r_ch]])
                    sm = fd.get("sat", 1.0)
                    if sm != 1.0:
                        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV).astype(np.float32)
                        hsv[:, :, 1] = np.clip(hsv[:, :, 1] * sm, 0, 255)
                        frame = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)
                strength = float(s.get("film_strength", 100)) / 100.0
                if strength < 0.99:
                    alpha = max(0.0, strength)
                    frame = cv2.addWeighted(original, 1.0 - alpha, frame, alpha, 0)

        ok, enc = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        return enc.tobytes() if ok else buf
    except Exception:
        return buf


# ── PIL-based snapshot filter ──────────────────────────────────────────────────
def _apply_filter(img, name):
    if name == "grayscale":
        return ImageOps.grayscale(img).convert("RGB")
    if name == "sepia":
        gray = ImageOps.grayscale(img)
        return ImageOps.colorize(gray, (100, 55, 10), (255, 235, 170))
    if name == "vivid":
        img = ImageEnhance.Color(img).enhance(1.9)
        img = ImageEnhance.Contrast(img).enhance(1.35)
        return ImageEnhance.Brightness(img).enhance(1.05)
    if name == "soft":
        img = img.filter(ImageFilter.GaussianBlur(radius=2.0))
        img = ImageEnhance.Contrast(img).enhance(0.72)
        return ImageEnhance.Brightness(img).enhance(1.12)
    if name == "sharp":
        img = ImageEnhance.Sharpness(img).enhance(4.0)
        return ImageEnhance.Contrast(img).enhance(1.15)
    return img


# ── Stream output ──────────────────────────────────────────────────────────────
class StreamOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()
        self.recorder = None  # wired up after VideoRecorder is created
        self._fps_lock  = threading.Lock()
        self._fps_count = 0
        self._fps_ts    = time.time()
        self.fps        = 0.0

    def write(self, buf):
        with cam_ctrl_lock:
            s = {k: cam_ctrl[k] for k in ("tint", "hflip", "vflip", "film_filter", "film_strength")}
        if s["tint"] or s["hflip"] or s["vflip"] or s["film_filter"] != "none":
            displayed = _apply_ocv(buf, s)
        else:
            displayed = buf

        with self.condition:
            self.frame = displayed
            self.condition.notify_all()
        if self.recorder:
            self.recorder.write(displayed)

        with self._fps_lock:
            self._fps_count += 1
            now = time.time()
            dt  = now - self._fps_ts
            if dt >= 2.0:
                self.fps        = round(self._fps_count / dt, 1)
                self._fps_count = 0
                self._fps_ts    = now


# ── Camera manager ─────────────────────────────────────────────────────────────
class CameraManager:
    def __init__(self):
        self.lock        = threading.Lock()
        self.output      = StreamOutput()
        self.res_key     = DEFAULT_RESOLUTION
        self.resolution  = RESOLUTIONS[self.res_key]
        self.model       = "C930e"
        self._stop       = threading.Event()
        self._restart    = threading.Event()
        self._current_cap = None
        threading.Thread(target=self._capture_loop, daemon=True, name="capture").start()

    def _open_cap(self):
        w, h = self.resolution
        cap = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
        if not cap.isOpened():
            cap.release()
            return None
        cap.set(cv2.CAP_PROP_FOURCC,      cv2.VideoWriter_fourcc(*"MJPG"))
        cap.set(cv2.CAP_PROP_FRAME_WIDTH,  w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, h)
        cap.set(cv2.CAP_PROP_FPS,          FPS)
        cap.set(cv2.CAP_PROP_BUFFERSIZE,   1)
        return cap

    def _capture_loop(self):
        backoff = 1
        while not self._stop.is_set():
            self._restart.clear()
            cap = self._open_cap()
            if cap is None:
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1
            with self.lock:
                self._current_cap = cap
            with cam_ctrl_lock:
                _init_ctrl = dict(cam_ctrl)
            self.apply_isp_controls(_init_ctrl)
            failures = 0
            while not self._stop.is_set() and not self._restart.is_set():
                ret, frame = cap.read()
                if not ret or frame is None:
                    failures += 1
                    if failures >= 5:
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self.output.write(buf.tobytes())
            cap.release()
            with self.lock:
                if self._current_cap is cap:
                    self._current_cap = None
            if not self._stop.is_set() and not self._restart.is_set():
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)

    def set_resolution(self, res_key):
        if res_key not in RESOLUTIONS or res_key == self.res_key:
            return False
        with self.lock:
            self.res_key    = res_key
            self.resolution = RESOLUTIONS[res_key]
        self._restart.set()
        return True

    def capture(self, prefix="Photo", filter_name="none", quality=85):
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{prefix}_{ts}.jpg"
        path = SNAPSHOT_DIR / filename
        with self.output.condition:
            self.output.condition.wait(timeout=3)
            frame = self.output.frame
        if not frame:
            return None
        img = Image.open(io.BytesIO(frame))
        if filter_name and filter_name != "none":
            img = _apply_filter(img, filter_name)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        path.write_bytes(buf.getvalue())

        # Post-processing: NR, CA correction, sharpening, grain.
        # Timelapse frames: run in background so capture timing isn't blocked.
        # Snapshots: run inline so the saved file is fully processed.
        if prefix == "tl":
            threading.Thread(
                target=postprocess_jpeg, args=(path, quality),
                kwargs={"fast": True}, daemon=True
            ).start()
        else:
            postprocess_jpeg(path, quality)

        return filename

    def apply_isp_controls(self, c):
        """Push V4L2 hardware controls. Called on user changes and after camera (re)open.

        C930e V4L2 ranges (from v4l2-ctl --list-ctrls):
          brightness/contrast/saturation/sharpness: 0–255, default 128
          white_balance_temperature: 2000–7500 K, default 4000
          gain: 0–255
          exposure_time_absolute: 3–2047 (×100 µs units)
        """
        with self.lock:
            cap = self._current_cap
        if cap is None:
            return
        try:
            # ── Exposure ──────────────────────────────────────────────────────
            exp = int(c.get("exposure_time", 0))
            if exp > 0:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 1)   # V4L2 Manual mode
                cap.set(cv2.CAP_PROP_EXPOSURE, max(3, exp // 100))
            else:
                cap.set(cv2.CAP_PROP_AUTO_EXPOSURE, 3)   # V4L2 Aperture Priority

            # ── Gain ──────────────────────────────────────────────────────────
            gain = float(c.get("analogue_gain", 0.0))
            if gain > 0:
                cap.set(cv2.CAP_PROP_GAIN, int(min(255, max(0, gain / 16.0 * 255))))

            # ── White balance ─────────────────────────────────────────────────
            # cv2.CAP_PROP_WHITE_BALANCE_BLUE_U maps to V4L2_CID_BLUE_BALANCE,
            # not white_balance_temperature — use v4l2-ctl directly instead.
            if c.get("awb_mode", "auto") == "auto":
                subprocess.run(
                    ["v4l2-ctl", "-d", DEVICE, "--set-ctrl=white_balance_automatic=1"],
                    capture_output=True, check=False,
                )
            else:
                kelvin = max(2000, min(7500, int(c.get("awb_kelvin", 5600))))
                subprocess.run(
                    ["v4l2-ctl", "-d", DEVICE,
                     f"--set-ctrl=white_balance_automatic=0,white_balance_temperature={kelvin}"],
                    capture_output=True, check=False,
                )

            # ── Brightness: UI −100…+100 → V4L2 0–255 (neutral 128) ──────────
            b = int(c.get("brightness", 0))
            cap.set(cv2.CAP_PROP_BRIGHTNESS, max(0, min(255, 128 + b * 127 // 100)))

            # ── Saturation: same mapping as brightness ────────────────────────
            sat = int(c.get("saturation", 0))
            cap.set(cv2.CAP_PROP_SATURATION, max(0, min(255, 128 + sat * 127 // 100)))

            # ── Sharpness: UI 0–4 (neutral 1.0) → V4L2 0–255 (neutral 128) ──
            sharp = float(c.get("sharpness", 1.0))
            sv = int(sharp * 128) if sharp <= 1.0 else int(128 + (sharp - 1.0) / 3.0 * 127)
            cap.set(cv2.CAP_PROP_SHARPNESS, max(0, min(255, sv)))

            # ── Contrast: same mapping as sharpness ───────────────────────────
            contrast = float(c.get("contrast", 1.0))
            cv_val = int(contrast * 128) if contrast <= 1.0 else int(128 + (contrast - 1.0) / 3.0 * 127)
            cap.set(cv2.CAP_PROP_CONTRAST, max(0, min(255, cv_val)))

        except Exception:
            pass

    def stop(self):
        self._stop.set()
        self._restart.set()
        with self.lock:
            cap = self._current_cap
            self._current_cap = None
        if cap is not None:
            try:
                cap.release()
            except Exception:
                pass


camera = CameraManager()
