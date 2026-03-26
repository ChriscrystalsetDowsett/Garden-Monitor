"""System stats — background collection thread + Pi info helpers."""
import time, threading, socket, subprocess
from pathlib import Path

import psutil

from .camera import camera

_stats_cache: dict = {}
_stats_lock  = threading.Lock()


def _wlan0_counters():
    """Return net counters for wlan0 only, falling back to aggregate."""
    return psutil.net_io_counters(pernic=True).get('wlan0') or psutil.net_io_counters()


def _wifi_link_stats():
    """Return (signal_dbm, tx_rate_mbps) from iw, or (None, None) on failure."""
    signal_dbm = None
    tx_rate_mbps = None
    try:
        for line in Path("/proc/net/wireless").read_text().splitlines():
            if "wlan0" in line:
                parts = line.split()
                signal_dbm = int(float(parts[3].rstrip('.')))
                break
    except Exception:
        pass
    try:
        out = subprocess.check_output(
            ["iw", "dev", "wlan0", "link"], text=True, stderr=subprocess.DEVNULL
        )
        for line in out.splitlines():
            if "tx bitrate" in line.lower():
                tx_rate_mbps = float(line.split()[2])
                break
    except Exception:
        pass
    return signal_dbm, tx_rate_mbps


def _collect_stats():
    """Background thread: refresh system stats every 5 seconds."""
    psutil.cpu_percent()                         # prime CPU measurement
    prev_net = _wlan0_counters()
    while True:
        time.sleep(5)
        mem  = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        net  = _wlan0_counters()
        temp = None
        try:
            temp = round(int(Path("/sys/class/thermal/thermal_zone0/temp").read_text()) / 1000, 1)
        except Exception:
            pass
        uptime = 0.0
        try:
            uptime = float(Path("/proc/uptime").read_text().split()[0])
        except Exception:
            pass
        recv_bps = (net.bytes_recv - prev_net.bytes_recv) / 5.0
        send_bps = (net.bytes_sent - prev_net.bytes_sent) / 5.0
        prev_net = net
        wifi_signal, wifi_rate_mbps = _wifi_link_stats()
        with _stats_lock:
            _stats_cache.update({
                "cpu_percent":    round(psutil.cpu_percent(), 1),
                "mem_used":       mem.used,
                "mem_total":      mem.total,
                "mem_percent":    round(mem.percent, 1),
                "disk_used":      disk.used,
                "disk_total":     disk.total,
                "disk_percent":   round(disk.percent, 1),
                "temperature":    temp,
                "uptime":         uptime,
                "fps":            camera.output.fps,
                "net_recv_bps":   recv_bps,
                "net_send_bps":   send_bps,
                "wifi_signal_dbm":  wifi_signal,
                "wifi_rate_mbps":   wifi_rate_mbps,
            })


threading.Thread(target=_collect_stats, daemon=True).start()


def get_stats():
    with _stats_lock:
        return dict(_stats_cache)


def get_pi_info():
    info = {}
    try:
        info["model"] = Path("/proc/device-tree/model").read_bytes().rstrip(b"\x00").decode()
    except Exception:
        info["model"] = "Raspberry Pi"
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                info["os"] = line.split("=", 1)[1].strip('"')
                break
    except Exception:
        info["os"] = "Unknown"
    try:
        info["kernel"] = subprocess.check_output(["uname", "-r"], text=True).strip()
    except Exception:
        info["kernel"] = "Unknown"
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        info["ip"] = s.getsockname()[0]
        s.close()
    except Exception:
        info["ip"] = "Unknown"
    info["camera"]     = camera.model
    info["resolution"] = camera.res_key
    return info
