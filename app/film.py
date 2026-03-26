"""Film simulation LUTs — precomputed at startup for fast per-frame lookup.

Each filter is a dict of per-channel uint8 LUTs (256-entry arrays) built from
(input, output) control points via linear interpolation. B&W filters use a
single 'curve' plus bw=True. Colour filters use r/g/b keys and an optional
saturation multiplier 'sat'.
"""
import numpy as np


def _interp_lut(pts):
    xs, ys = zip(*pts)
    return np.clip(np.interp(np.arange(256), xs, ys), 0, 255).astype(np.uint8)


def _build_film_luts():
    f = {}

    # Kodak Portra 400 — warm, lifted shadows, gently desaturated.
    f['portra'] = dict(
        r=_interp_lut([(0,20),(64,92),(128,152),(192,210),(255,245)]),
        g=_interp_lut([(0,15),(64,86),(128,148),(192,207),(255,243)]),
        b=_interp_lut([(0,10),(64,72),(128,132),(192,185),(255,218)]),
        sat=0.82,
    )

    # Fuji Velvia 50 — vivid, punchy, deep shadows.
    f['velvia'] = dict(
        r=_interp_lut([(0,0),(64,54),(128,138),(192,213),(255,255)]),
        g=_interp_lut([(0,0),(64,58),(128,142),(192,216),(255,255)]),
        b=_interp_lut([(0,0),(64,62),(128,148),(192,220),(255,255)]),
        sat=1.7,
    )

    # Ilford HP5 — classic panchromatic B&W.
    hp5_curve = _interp_lut([(0,5),(64,72),(128,130),(192,194),(255,250)])
    f['hp5'] = dict(bw=True, curve=hp5_curve)

    # Cinestill 800T — tungsten-balanced, cyan shadows.
    f['cinestill'] = dict(
        r=_interp_lut([(0,5),(64,68),(128,136),(192,200),(255,244)]),
        g=_interp_lut([(0,8),(64,78),(128,143),(192,207),(255,248)]),
        b=_interp_lut([(0,24),(64,102),(128,166),(192,222),(255,252)]),
        sat=0.88,
    )

    # Kodak Tri-X 400 — high-contrast B&W street film.
    trix_curve = _interp_lut([(0,0),(48,32),(128,138),(208,228),(255,255)])
    f['trix'] = dict(bw=True, curve=trix_curve)

    # Fuji Provia 100F — accurate, neutral, slightly cool.
    f['provia'] = dict(
        r=_interp_lut([(0,0),(64,64),(128,130),(192,196),(255,255)]),
        g=_interp_lut([(0,0),(64,66),(128,132),(192,198),(255,255)]),
        b=_interp_lut([(0,0),(64,70),(128,138),(192,204),(255,255)]),
        sat=1.05,
    )

    # Kodak Ektar 100 — hyper-saturated, vivid reds.
    f['ektar'] = dict(
        r=_interp_lut([(0,0),(64,64),(128,136),(192,204),(255,255)]),
        g=_interp_lut([(0,0),(64,63),(128,130),(192,198),(255,255)]),
        b=_interp_lut([(0,0),(64,58),(128,124),(192,190),(255,248)]),
        sat=1.55,
    )

    # Agfa Vista 200 — warm, faded vintage look.
    f['agfa'] = dict(
        r=_interp_lut([(0,25),(64,88),(128,148),(192,204),(255,244)]),
        g=_interp_lut([(0,20),(64,93),(128,158),(192,213),(255,246)]),
        b=_interp_lut([(0,10),(64,65),(128,116),(192,166),(255,206)]),
        sat=1.12,
    )

    return f


FILM_FILTERS = _build_film_luts()
