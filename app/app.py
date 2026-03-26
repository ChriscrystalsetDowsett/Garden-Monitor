"""Flask application — all HTTP routes."""
import os
from pathlib import Path

from flask import Flask, Response, render_template, jsonify, request, send_from_directory

from .config import SNAPSHOT_DIR, VIDEOS_DIR, CAM_CTRL_DEFAULTS
from .camera import camera, cam_ctrl, cam_ctrl_lock
from .timelapse import timelapse, get_compile_status
from .recorder import video_recorder
from .stats import get_stats, get_pi_info

# Wire the recorder into the camera stream so it captures what the user sees
camera.output.recorder = video_recorder

app = Flask(__name__, template_folder=str(Path(__file__).parent.parent / "templates"))


# ── Stream ─────────────────────────────────────────────────────────────────────

def gen_frames():
    while True:
        with camera.output.condition:
            camera.output.condition.wait(timeout=2)
            frame = camera.output.frame
        if frame:
            yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/stream")
def stream():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")


# ── Resolution ─────────────────────────────────────────────────────────────────

@app.route("/api/resolution", methods=["POST"])
def set_resolution():
    res = request.json.get("resolution")
    ok = camera.set_resolution(res)
    return jsonify({"ok": ok, "resolution": camera.res_key})


# ── Snapshot ───────────────────────────────────────────────────────────────────

@app.route("/api/snapshot", methods=["POST"])
def snapshot():
    data        = request.json or {}
    filter_name = data.get("filter", "none")
    quality     = int(data.get("quality", 85))
    filename    = camera.capture(filter_name=filter_name, quality=quality)
    if filename:
        return jsonify({"ok": True, "filename": filename})
    return jsonify({"ok": False}), 500


@app.route("/snapshots/<filename>")
def serve_snapshot(filename):
    return send_from_directory(SNAPSHOT_DIR, filename)


@app.route("/api/snapshot/<filename>", methods=["DELETE"])
def delete_snapshot(filename):
    if "/" in filename or ".." in filename:
        return jsonify({"ok": False}), 400
    path = SNAPSHOT_DIR / filename
    if not path.exists():
        return jsonify({"ok": False}), 404
    path.unlink()
    return jsonify({"ok": True})


# ── Gallery ────────────────────────────────────────────────────────────────────

@app.route("/api/gallery")
def gallery():
    entries = []
    with os.scandir(SNAPSHOT_DIR) as it:
        for entry in it:
            if entry.name.endswith(".jpg"):
                st = entry.stat()
                entries.append((st.st_mtime, entry.name, st.st_size))
    entries.sort(key=lambda x: x[0], reverse=True)
    return jsonify([{"filename": n, "size": s} for _, n, s in entries[:100]])


# ── Timelapse ──────────────────────────────────────────────────────────────────

@app.route("/api/timelapse/start", methods=["POST"])
def tl_start():
    interval = request.json.get("interval", 5)
    duration = request.json.get("duration", 0)
    timelapse.start(interval, duration)
    return jsonify({"ok": True, **timelapse.status()})


@app.route("/api/timelapse/stop", methods=["POST"])
def tl_stop():
    timelapse.stop()
    return jsonify({"ok": True, **timelapse.status()})


@app.route("/api/timelapse/status")
def tl_status():
    return jsonify(timelapse.status())


@app.route("/api/timelapse/compile_status")
def tl_compile_status():
    return jsonify(get_compile_status())


# ── Recording ──────────────────────────────────────────────────────────────────

@app.route("/api/record/start", methods=["POST"])
def record_start():
    crf = (request.json or {}).get("quality", 23)
    ok  = video_recorder.start(crf=int(crf))
    return jsonify({"ok": ok, **video_recorder.status()})


@app.route("/api/record/stop", methods=["POST"])
def record_stop():
    filename = video_recorder.stop()
    return jsonify({"ok": bool(filename), "filename": filename})


@app.route("/api/record/status")
def record_status():
    return jsonify(video_recorder.status())


# ── Videos ─────────────────────────────────────────────────────────────────────

@app.route("/api/videos")
def list_videos():
    entries = []
    with os.scandir(VIDEOS_DIR) as it:
        for entry in it:
            if entry.name.endswith(".mp4"):
                st = entry.stat()
                entries.append((st.st_mtime, entry.name, st.st_size))
    entries.sort(key=lambda x: x[0], reverse=True)
    return jsonify([{"filename": n, "size": s} for _, n, s in entries[:50]])


@app.route("/videos/<filename>")
def serve_video(filename):
    return send_from_directory(VIDEOS_DIR, filename)


# ── Camera controls ────────────────────────────────────────────────────────────

@app.route("/api/camera_controls", methods=["POST"])
def set_cam_controls():
    data = request.json or {}
    with cam_ctrl_lock:
        for k, v in data.items():
            if k in CAM_CTRL_DEFAULTS:
                cam_ctrl[k] = v
        current = dict(cam_ctrl)
    camera.apply_isp_controls(current)
    return jsonify({"ok": True})


@app.route("/api/camera_controls/defaults")
def cam_ctrl_defaults():
    return jsonify(CAM_CTRL_DEFAULTS)


# ── System stats + info ────────────────────────────────────────────────────────

@app.route("/api/stats")
def stats():
    return jsonify(get_stats())


@app.route("/api/info")
def pi_info():
    return jsonify(get_pi_info())
