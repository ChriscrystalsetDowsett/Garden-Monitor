"""
Automatic post-processing for photos and timelapse frames.

Pipeline (applied in LAB colour space):
  1. Mild chroma smoothing  — bilateral filter on A/B channels only;
                              reduces JPEG colour-block artefacts without
                              smearing colour edges or flattening hues.
                              Bilateral is edge-preserving and appropriate
                              for JPEG-compressed input; NLM is not.
  2. Output sharpening      — unsharp mask on L only; edge-localised and
                              threshold-gated to avoid lifting noise in
                              flat regions.

Removed from previous version:
  - CA correction: calibrated for IMX219, not C930e; the 0.1 % scale
    offsets are sub-pixel on this sensor and the remap's bilinear
    interpolation adds softening without correcting a real defect.
  - NLM noise reduction: wrong tool for JPEG-compressed input — the
    algorithm conflates DCT block artefacts with noise and produces
    watercolour smearing on colour channels.
  - Film grain: objectively degrades quality (adds ~1.2 dB RMS noise,
    increases re-compressed file size). Its only purpose was to mask
    over-smoothing from NLM; removing NLM removes the need for it.

Processing time at 1280×720: ~0.05 s per image.
Images are processed in-place (path is overwritten).
"""

import cv2
import numpy as np
from pathlib import Path

try:
    import piexif as _piexif
    _PIEXIF_OK = True
except ImportError:
    _PIEXIF_OK = False

# ── Tuning constants ───────────────────────────────────────────────────────────
# Chroma smoothing — bilateral filter on A/B (colour) channels only.
# d=3 is a 3×3 neighbourhood — conservative, preserves fine colour edges.
# sigmaColor=5 means only pixels within 5 levels are blended — very selective.
_CHROMA_D            = 3
_CHROMA_SIGMA_COLOR  = 5
_CHROMA_SIGMA_SPACE  = 3

# Output sharpening — unsharp mask on L (luminance) channel.
_USM_SIGMA     = 1.0   # Gaussian blur radius for the residual mask
_USM_AMOUNT    = 0.50  # sharpening weight (0.5 = 50 % of the edge residual)
_USM_THRESHOLD = 8     # minimum edge magnitude to sharpen (skips noise/flat areas)


def _unsharp_mask(l_channel: np.ndarray) -> np.ndarray:
    """Edge-localised sharpening on luminance only."""
    blurred   = cv2.GaussianBlur(l_channel, (0, 0), _USM_SIGMA)
    diff      = l_channel.astype(np.int16) - blurred.astype(np.int16)
    mask      = (np.abs(diff) > _USM_THRESHOLD).astype(np.float32)
    sharpened = l_channel.astype(np.float32) + _USM_AMOUNT * diff * mask
    return np.clip(sharpened, 0, 255).astype(np.uint8)


# ── Public entry point ────────────────────────────────────────────────────────
def postprocess_jpeg(path: Path, quality: int = 92, fast: bool = False) -> None:
    """
    Load *path*, apply the post-processing pipeline, overwrite *path* in place.
    Silently no-ops on any error so it never breaks capture flow.

    fast=True skips sharpening for timelapse frames where throughput matters.
    """
    try:
        path = Path(path)
        data = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        bgr  = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if bgr is None:
            return

        # ── 1. Convert to LAB ────────────────────────────────────────────────
        lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
        l, a, b_ch = cv2.split(lab)

        # ── 2. Mild chroma smoothing (JPEG colour-block artefact reduction) ──
        # Bilateral on A/B only — L channel (detail) is untouched here.
        a    = cv2.bilateralFilter(a,    d=_CHROMA_D,
                                   sigmaColor=_CHROMA_SIGMA_COLOR,
                                   sigmaSpace=_CHROMA_SIGMA_SPACE)
        b_ch = cv2.bilateralFilter(b_ch, d=_CHROMA_D,
                                   sigmaColor=_CHROMA_SIGMA_COLOR,
                                   sigmaSpace=_CHROMA_SIGMA_SPACE)

        # ── 3. Output sharpening (skipped on timelapse fast path) ───────────
        if not fast:
            l = _unsharp_mask(l)

        # ── 4. Merge and save, preserving EXIF ──────────────────────────────
        lab_out = cv2.merge([l, a, b_ch])
        bgr_out = cv2.cvtColor(lab_out, cv2.COLOR_LAB2BGR)
        ok, buf = cv2.imencode(".jpg", bgr_out, [cv2.IMWRITE_JPEG_QUALITY, quality])
        if ok:
            raw_bytes = buf.tobytes()
            if _PIEXIF_OK:
                try:
                    exif_bytes = _piexif.load(str(path))
                    raw_bytes  = _piexif.insert(_piexif.dump(exif_bytes), raw_bytes)
                except Exception:
                    pass
            path.write_bytes(raw_bytes)

    except Exception:
        pass   # never crash capture; just leave the original file
