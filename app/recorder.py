"""Video recorder — captures live MJPEG stream to file and converts to MP4."""
import threading, time, subprocess
from datetime import datetime
from pathlib import Path

from .config import VIDEOS_DIR


def _convert_recording(src, dst, fps, crf=23):
    """Convert a raw MJPEG dump to H.264 MP4; delete source on success."""
    result = subprocess.run(
        ["ffmpeg", "-y", "-r", str(fps), "-f", "mjpeg",
         "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", str(crf),
         str(dst)],
        capture_output=True, timeout=300,
    )
    if result.returncode == 0:
        src.unlink(missing_ok=True)


class VideoRecorder:
    """Records the live MJPEG stream to a .mjpeg file then converts to MP4."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self._file = None
        self.filename = None
        self.start_time = None
        self.frame_count = 0

    def start(self, crf=23):
        with self._lock:
            if self.running:
                return False
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.filename = f"Video_{ts}.mjpeg"
            self._file = open(VIDEOS_DIR / self.filename, "wb")
            self.frame_count = 0
            self.start_time = time.time()
            self.running = True
            self.crf = crf
        return True

    def write(self, frame):
        with self._lock:
            if self.running and self._file:
                self._file.write(frame)
                self.frame_count += 1

    def stop(self):
        with self._lock:
            if not self.running:
                return None
            self.running = False
            duration = time.time() - self.start_time if self.start_time else 0
            fc        = self.frame_count
            fname     = self.filename
            if self._file:
                self._file.close()
                self._file = None
        if fname and fc > 0 and duration > 0:
            fps = max(1, round(fc / duration))
            src = VIDEOS_DIR / fname
            dst = VIDEOS_DIR / fname.replace(".mjpeg", ".mp4")
            threading.Thread(
                target=_convert_recording, args=(src, dst, fps, self.crf), daemon=True
            ).start()
        return fname

    def status(self):
        with self._lock:
            return {
                "running":     self.running,
                "filename":    self.filename,
                "duration":    round(time.time() - self.start_time, 1)
                               if self.start_time and self.running else 0,
                "frame_count": self.frame_count,
            }


video_recorder = VideoRecorder()
