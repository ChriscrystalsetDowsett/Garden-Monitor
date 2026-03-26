"""Dashboard blueprint — multi-camera overview and API proxy."""
import requests as _requests
from flask import Blueprint, Response, jsonify, render_template, request

from .config import CAMERAS

dashboard = Blueprint("dashboard", __name__)

_TIMEOUT = 8   # seconds — generous enough for a Pi, not so long it hangs the UI


@dashboard.route("/dashboard")
def index():
    return render_template("dashboard.html", cameras=CAMERAS)


@dashboard.route(
    "/dashboard/cam/<int:idx>/proxy/<path:api_path>",
    methods=["GET", "POST", "DELETE"],
)
def cam_proxy(idx, api_path):
    """Proxy API calls to a camera Pi, avoiding browser CORS restrictions."""
    if idx < 0 or idx >= len(CAMERAS):
        return jsonify({"error": "invalid camera index"}), 404

    cam = CAMERAS[idx]
    url = f"http://{cam['host']}:{cam['port']}/{api_path}"

    # Only forward Content-Type; never forward Host or other hop-by-hop headers.
    fwd_headers = {}
    if request.content_type:
        fwd_headers["Content-Type"] = request.content_type

    try:
        resp = _requests.request(
            method=request.method,
            url=url,
            data=request.get_data(),
            headers=fwd_headers,
            timeout=_TIMEOUT,
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
