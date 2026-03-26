"""Camera hardware management, picamera2 post-processing pipeline, and stream output."""
import io, time, threading
from datetime import datetime

import cv2
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps
from picamera2 import Picamera2

from .config import SNAPSHOT_DIR, RESOLUTIONS, CAM_CTRL_DEFAULTS, DEFAULT_RESOLUTION
from .film import FILM_FILTERS
from .postprocess import postprocess_jpeg

FPS          = 24
JPEG_QUALITY = 85

# ── Live camera controls (per-frame effects + ISP) ─────────────────────────────
# Shared mutable state; protected by cam_ctrl_lock.
cam_ctrl = dict(CAM_CTRL_DEFAULTS)
cam_ctrl_lock = threading.Lock()


# ── OpenCV post-processing ─────────────────────────────────────────────────────
def _apply_ocv(buf, s):
    """Post-processing: tint shift, flip, film simulation."""
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
        self.model       = "Unknown"
        self._stop       = threading.Event()
        self._restart    = threading.Event()
        self._picam2     = None
        threading.Thread(target=self._capture_loop, daemon=True, name="capture").start()

    def _open_camera(self):
        w, h = self.resolution
        try:
            picam2 = Picamera2()
            self.model = picam2.camera_properties.get("Model", "Unknown").upper()
            config = picam2.create_video_configuration(
                main={"format": "RGB888", "size": (w, h)},
                controls={"FrameRate": float(FPS)},
                buffer_count=2,
            )
            picam2.configure(config)
            picam2.start()
            return picam2
        except Exception:
            return None

    def _capture_loop(self):
        backoff = 1
        while not self._stop.is_set():
            self._restart.clear()
            picam2 = self._open_camera()
            if picam2 is None:
                if self._stop.wait(backoff):
                    break
                backoff = min(backoff * 2, 30)
                continue
            backoff = 1
            with self.lock:
                self._picam2 = picam2
            with cam_ctrl_lock:
                _init_ctrl = dict(cam_ctrl)
            self.apply_isp_controls(_init_ctrl)
            failures = 0
            while not self._stop.is_set() and not self._restart.is_set():
                try:
                    frame = picam2.capture_array("main")
                except Exception:
                    failures += 1
                    if failures >= 5:
                        break
                    time.sleep(0.05)
                    continue
                failures = 0
                # picamera2 RGB888 = V4L2 BGR24: array is already BGR, OpenCV-native
                ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
                if ok:
                    self.output.write(buf.tobytes())
            try:
                picam2.stop()
                picam2.close()
            except Exception:
                pass
            with self.lock:
                if self._picam2 is picam2:
                    self._picam2 = None
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
        """Push picamera2 ISP controls to the camera module.

        picamera2 control ranges:
          Brightness:        -1.0 to 1.0   (0.0 = neutral)
          Saturation:         0.0 to 2.0   (1.0 = neutral)
          Sharpness:          0.0 to 16.0  (1.0 = neutral)
          Contrast:           0.0 to 32.0  (1.0 = neutral)
          ExposureTime:       microseconds (requires AeEnable=False)
          AnalogueGain:       float        (requires AeEnable=False)
          AwbEnable:          bool
          ColourTemperature:  2000-7500 K  (requires AwbEnable=False)
        """
        with self.lock:
            picam2 = self._picam2
        if picam2 is None:
            return
        try:
            controls = {}

            # ── Exposure ──────────────────────────────────────────────────────
            exp = int(c.get("exposure_time", 0))
            if exp > 0:
                controls["AeEnable"] = False
                controls["ExposureTime"] = exp
            else:
                controls["AeEnable"] = True

            # ── Gain ──────────────────────────────────────────────────────────
            gain = float(c.get("analogue_gain", 0.0))
            if gain > 0:
                controls["AnalogueGain"] = gain

            # ── White balance ─────────────────────────────────────────────────
            if c.get("awb_mode", "auto") == "auto":
                controls["AwbEnable"] = True
            else:
                controls["AwbEnable"] = False
                kelvin = max(2000, min(7500, int(c.get("awb_kelvin", 5600))))
                controls["ColourTemperature"] = kelvin

            # ── Brightness: UI −100…+100 → picamera2 −1.0…+1.0 ──────────────
            b = int(c.get("brightness", 0))
            controls["Brightness"] = max(-1.0, min(1.0, b / 100.0))

            # ── Saturation: UI −100…+100 → picamera2 0.0…2.0 (neutral 1.0) ──
            sat = int(c.get("saturation", 0))
            controls["Saturation"] = max(0.0, min(2.0, 1.0 + sat / 100.0))

            # ── Sharpness: UI 0–4 → picamera2 0.0–16.0 (neutral 1.0) ─────────
            sharp = float(c.get("sharpness", 1.0))
            controls["Sharpness"] = max(0.0, min(16.0, sharp))

            # ── Contrast: UI 0–4 → picamera2 0.0–32.0 (neutral 1.0) ──────────
            contrast = float(c.get("contrast", 1.0))
            controls["Contrast"] = max(0.0, min(32.0, contrast))

            picam2.set_controls(controls)
        except Exception:
            pass

    def stop(self):
        self._stop.set()
        self._restart.set()
        with self.lock:
            picam2 = self._picam2
            self._picam2 = None
        if picam2 is not None:
            try:
                picam2.stop()
                picam2.close()
            except Exception:
                pass


camera = CameraManager()
