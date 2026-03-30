"""Video recorder — captures live MJPEG stream to file and converts to MP4."""
import threading, time, subprocess
from datetime import datetime
from pathlib import Path

from .config import VIDEOS_DIR

AUDIO_DEVICE = 'plughw:C930e,0'


def _extract_thumbnail(mp4_path: Path) -> None:
    """Extract a single frame from 10 % into the video as a JPEG thumbnail."""
    thumb = mp4_path.with_suffix(".thumb.jpg")
    try:
        subprocess.run(
            ["ffmpeg", "-y",
             "-ss", "0.1",           # start slightly in so black frames are avoided
             "-i", str(mp4_path),
             "-vf", "thumbnail=100", # pick best frame from first 100
             "-frames:v", "1",
             "-q:v", "3",            # JPEG quality (2=best, 5=good)
             str(thumb)],
            capture_output=True, timeout=30,
        )
    except Exception:
        pass


def _convert_recording(src, dst, fps, crf=23, audio_src=None, start_ts=None):
    """Convert a raw MJPEG dump to H.264 MP4; delete source on success."""
    cmd = ['ffmpeg', '-y', '-r', str(fps), '-f', 'mjpeg', '-i', str(src)]
    if audio_src and Path(audio_src).exists() and Path(audio_src).stat().st_size > 0:
        # Audio filter chain applied at encode time:
        #   highpass=f=80   — cut rumble and handling noise below 80 Hz
        #   afftdn=nf=-25   — FFT-based noise reduction (−25 dB noise floor)
        #   loudnorm        — EBU R128 loudness normalisation (consistent levels)
        #   acompressor     — gentle dynamic compression to even out peaks/quiet
        audio_filters = (
            "highpass=f=80,"
            "afftdn=nf=-25,"
            "loudnorm,"
            "acompressor=threshold=-18dB:ratio=3:attack=5:release=50"
        )
        cmd += ['-i', str(audio_src),
                '-af', audio_filters,
                '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '1',
                '-shortest']
    cmd += ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', str(crf)]
    if start_ts:
        # start_ts format: "YYYY-MM-DD_HH-MM-SS" → ISO 8601 for ffmpeg
        iso_dt = start_ts[:10] + "T" + start_ts[11:].replace("-", ":")
        cmd += [
            '-metadata', f'creation_time={iso_dt}',
            '-metadata', f'title={dst.stem}',
            '-metadata', 'comment=Garden Monitor Video',
            '-metadata', 'encoder=Garden Monitor',
        ]
    cmd += [str(dst)]
    result = subprocess.run(cmd, capture_output=True, timeout=300)
    if result.returncode == 0:
        src.unlink(missing_ok=True)
        if audio_src:
            Path(audio_src).unlink(missing_ok=True)
        _extract_thumbnail(dst)


class VideoRecorder:
    """Records the live MJPEG stream to a .mjpeg file then converts to MP4."""

    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self._file = None
        self.filename = None
        self.start_time = None
        self.frame_count = 0
        self._audio_proc = None
        self._audio_file = None

    def start(self, crf=23, audio=False):
        with self._lock:
            if self.running:
                return False
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
            self.filename = f"Video_{ts}.mjpeg"
            self._file = open(VIDEOS_DIR / self.filename, "wb")
            self.frame_count = 0
            self.start_time = time.time()
            self._start_ts  = ts
            self.running = True
            self.crf = crf
            self._audio_proc = None
            self._audio_file = None
            if audio:
                audio_path = VIDEOS_DIR / f"Video_{ts}.wav"
                self._audio_file = str(audio_path)
                try:
                    self._audio_proc = subprocess.Popen(
                        ['ffmpeg', '-y',
                         '-f', 'alsa', '-ar', '48000', '-ac', '2',
                         '-i', AUDIO_DEVICE,
                         str(audio_path)],
                        stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                    )
                except Exception:
                    self._audio_proc = None
                    self._audio_file = None
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
            duration   = time.time() - self.start_time if self.start_time else 0
            fc         = self.frame_count
            fname      = self.filename
            start_ts   = getattr(self, "_start_ts", None)
            audio_proc = self._audio_proc
            audio_file = self._audio_file
            self._audio_proc = None
            self._audio_file = None
            if self._file:
                self._file.close()
                self._file = None
        if audio_proc:
            audio_proc.terminate()
            try:
                audio_proc.wait(timeout=3)
            except Exception:
                audio_proc.kill()
        if fname and fc > 0 and duration > 0:
            fps = max(1, round(fc / duration))
            src = VIDEOS_DIR / fname
            dst = VIDEOS_DIR / fname.replace(".mjpeg", ".mp4")
            threading.Thread(
                target=_convert_recording,
                args=(src, dst, fps, self.crf, audio_file, start_ts),
                daemon=True,
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
