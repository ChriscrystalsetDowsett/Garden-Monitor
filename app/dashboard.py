"""Dashboard blueprint — multi-camera overview, API proxy, and full UI proxy."""
import re as _re
import time as _time
import requests as _requests
import cv2 as _cv2
import numpy as _np
from flask import Blueprint, Response, jsonify, render_template, request, stream_with_context

from .config import CAMERAS, TILE_QUALITY

dashboard = Blueprint("dashboard", __name__)

_API_TIMEOUT    = 8    # seconds for regular JSON API calls
_STREAM_CONNECT = 10   # seconds to establish a frame connection

# fps and JPEG quality per quality level
_QUALITY = {
    "low":    (8,  45),
    "medium": (15, 68),
    "high":   (24, 85),
}


def _cam_base(idx):
    cam = CAMERAS[idx]
    return f"http://{cam['host']}:{cam['port']}"


# ── Dashboard grid ─────────────────────────────────────────────────────────────

@dashboard.route("/dashboard")
def index():
    return render_template("dashboard.html", cameras=CAMERAS, tile_quality=TILE_QUALITY)


# ── Quick-action API proxy (used by dashboard JS) ──────────────────────────────

@dashboard.route(
    "/dashboard/cam/<int:idx>/proxy/<path:api_path>",
    methods=["GET", "POST", "DELETE"],
)
def cam_proxy(idx, api_path):
    """Proxy API + stream calls from the dashboard grid to a camera Pi."""
    if idx < 0 or idx >= len(CAMERAS):
        return jsonify({"error": "invalid camera index"}), 404
    return _proxy(idx, api_path)


# ── Full camera UI ─────────────────────────────────────────────────────────────

@dashboard.route("/camera/<int:idx>", methods=["GET"])
@dashboard.route("/camera/<int:idx>/", methods=["GET"])
def camera_full(idx):
    """
    Serve the camera Pi's full UI with all URLs rewritten to pass through
    this server, so the page works from any domain (local or public).
    """
    if idx < 0 or idx >= len(CAMERAS):
        return jsonify({"error": "camera not found"}), 404

    try:
        resp = _requests.get(_cam_base(idx) + "/", timeout=_API_TIMEOUT)
    except (_requests.exceptions.ConnectionError, _requests.exceptions.Timeout):
        return "Camera offline", 502

    prefix = f"/camera/{idx}"
    html   = resp.text

    # ── Rewrite root-relative src/href attributes in HTML ─────────────────────
    # Matches src="/..." and href="/..." but not src="//cdn..." or src="data:..."
    html = _re.sub(
        r'((?:src|href)=")(/[^/"#][^"]*)"',
        lambda m: f'{m.group(1)}{prefix}{m.group(2)}"',
        html,
    )

    # ── Inject JS shims before </head> ────────────────────────────────────────
    # Rewrites root-relative URLs at runtime for:
    #   - fetch() API calls
    #   - img.src / anchor.href direct property assignments
    #   - element.setAttribute('src', ...) calls
    shim = f"""<script>
(function() {{
  var px = '{prefix}';
  function _rw(v) {{
    return (typeof v === 'string' && v.charAt(0) === '/' && v.indexOf(px) !== 0)
      ? px + v : v;
  }}

  /* fetch() */
  var _f = window.fetch;
  window.fetch = function(u, o) {{ return _f.call(this, _rw(u), o); }};

  /* img.src and similar direct property assignments */
  ['src', 'href'].forEach(function(prop) {{
    var targets = [HTMLImageElement, HTMLAnchorElement, HTMLSourceElement];
    targets.forEach(function(Ctor) {{
      var d = Object.getOwnPropertyDescriptor(Ctor.prototype, prop);
      if (d && d.set) {{
        Object.defineProperty(Ctor.prototype, prop, {{
          set: function(v) {{ d.set.call(this, _rw(v)); }},
          get: d.get,
          configurable: true,
        }});
      }}
    }});
  }});

  /* element.setAttribute('src'/'href', ...) */
  var _sa = Element.prototype.setAttribute;
  Element.prototype.setAttribute = function(n, v) {{
    if (n === 'src' || n === 'href') v = _rw(v);
    return _sa.call(this, n, v);
  }};
}})();
</script>"""
    html = html.replace("</head>", shim + "\n</head>", 1)

    return Response(html, content_type="text/html; charset=utf-8")


@dashboard.route("/camera/<int:idx>/<path:path>", methods=["GET", "POST", "DELETE"])
def camera_proxy(idx, path):
    """Proxy all sub-requests (stream, API, snapshots, videos) for the full UI."""
    if idx < 0 or idx >= len(CAMERAS):
        return jsonify({"error": "camera not found"}), 404
    return _proxy(idx, path)


# ── Shared proxy helper ────────────────────────────────────────────────────────

def _proxy(idx, path):
    """
    Forward a request to the target camera Pi.
    MJPEG streams are chunked without buffering; everything else is buffered.
    """
    url = f"{_cam_base(idx)}/{path}"

    fwd_headers = {}
    if request.content_type:
        fwd_headers["Content-Type"] = request.content_type

    # Pull-based MJPEG stream — fetches the latest frame on each tick.
    # This avoids frame-queue buildup that causes lag over the internet.
    # Re-encoding at lower quality happens here so camera Pis stay stateless.
    if path == "stream":
        quality   = request.args.get("quality", "medium")
        fps, q    = _QUALITY.get(quality, _QUALITY["medium"])
        min_delay = 1.0 / fps
        frame_url = f"{_cam_base(idx)}/api/frame"

        def _encode(data):
            """Re-encode JPEG at target quality if meaningfully below camera default."""
            if q >= 83:
                return data
            arr = _np.frombuffer(data, dtype=_np.uint8)
            img = _cv2.imdecode(arr, _cv2.IMREAD_COLOR)
            if img is None:
                return data
            ok, enc = _cv2.imencode(".jpg", img, [_cv2.IMWRITE_JPEG_QUALITY, q])
            return enc.tobytes() if ok else data

        def _generate():
            session = _requests.Session()
            try:
                err_count = 0
                while True:
                    t0 = _time.monotonic()
                    try:
                        r = session.get(frame_url, timeout=3)
                        if r.status_code == 200 and r.content:
                            yield (
                                b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                                + _encode(r.content) + b"\r\n"
                            )
                            err_count = 0
                        else:
                            err_count += 1
                            _time.sleep(min(err_count, 5) * 0.5)
                            continue
                    except _requests.exceptions.RequestException:
                        err_count += 1
                        _time.sleep(min(err_count, 5) * 0.5)
                        continue
                    elapsed = _time.monotonic() - t0
                    gap = min_delay - elapsed
                    if gap > 0:
                        _time.sleep(gap)
            except Exception:
                return
            finally:
                session.close()

        return Response(
            stream_with_context(_generate()),
            content_type="multipart/x-mixed-replace; boundary=frame",
            direct_passthrough=True,
            headers={"X-Accel-Buffering": "no"},
        )

    # Everything else — JSON API, snapshots, videos, static assets.
    try:
        resp = _requests.request(
            method=request.method,
            url=url,
            data=request.get_data(),
            headers=fwd_headers,
            timeout=_API_TIMEOUT,
        )
        return Response(
            resp.content,
            status=resp.status_code,
            content_type=resp.headers.get("Content-Type", "application/json"),
        )
    except _requests.exceptions.ConnectionError:
        return jsonify({"error": "offline"}), 502
    except _requests.exceptions.Timeout:
        return jsonify({"error": "timeout"}), 504
