"""Film simulation LUTs — precomputed at startup for fast per-frame lookup.

Each filter is a dict with:
  Colour films: r/g/b   — per-channel uint8 LUTs (256 entries)
                sat      — saturation multiplier (applied in HSV)
  B&W films:   bw=True
               weights  — (r, g, b) float channel-mixing ratios that mimic
                          each stock's spectral sensitivity before grey
                          conversion; must sum to 1.0
               curve    — uint8 LUT applied to the mixed grey channel

Curves are built with a monotone cubic Hermite spline (Fritsch-Carlson
method) rather than linear interpolation.  This correctly reproduces the
characteristic toe (gentle rolloff in shadows) and shoulder (graceful
highlight compression) of real film stocks without the overshoots that
natural cubic splines can produce.
"""
import numpy as np


def _cspline_lut(pts):
    """Monotone cubic Hermite spline (Fritsch-Carlson) through control
    points → uint8 LUT[256].

    pts — list of (input, output) pairs in ascending input order.
    Inputs outside the range of pts are clamped to the endpoint values.
    """
    xs = np.array([p[0] for p in pts], dtype=float)
    ys = np.array([p[1] for p in pts], dtype=float)
    n  = len(xs)

    dx    = np.diff(xs)
    dy    = np.diff(ys)
    delta = dy / dx

    # Initial tangents: arithmetic mean of adjacent slopes
    m       = np.empty(n)
    m[1:-1] = (delta[:-1] + delta[1:]) / 2.0
    m[0]    = delta[0]
    m[-1]   = delta[-1]

    # Fritsch-Carlson monotonicity correction
    for k in range(n - 1):
        if abs(delta[k]) < 1e-10:
            m[k] = m[k + 1] = 0.0
            continue
        a = m[k]     / delta[k]
        b = m[k + 1] / delta[k]
        h = a * a + b * b
        if h > 9.0:
            t        = 3.0 / h ** 0.5
            m[k]     = t * a * delta[k]
            m[k + 1] = t * b * delta[k]

    # Evaluate at each integer 0–255 (vectorised)
    xq      = np.arange(256, dtype=float)
    xc      = np.clip(xq, xs[0], xs[-1])
    seg     = np.clip(np.searchsorted(xs[1:], xc, side='left'), 0, n - 2)
    x0, x1  = xs[seg], xs[seg + 1]
    y0, y1  = ys[seg], ys[seg + 1]
    m0, m1  = m[seg],  m[seg + 1]
    hs      = x1 - x0
    t       = (xc - x0) / hs
    yq      = ((2*t**3 - 3*t**2 + 1) * y0
               + (t**3 - 2*t**2 + t) * hs * m0
               + (-2*t**3 + 3*t**2)  * y1
               + (t**3 - t**2)       * hs * m1)
    return np.clip(yq, 0, 255).astype(np.uint8)


def _build_film_luts():
    f = {}

    # ── Kodak Portra 400 ─────────────────────────────────────────────────────
    # C-41 negative.  Lifted shadows (~16–18 levels), warm midtones, graceful
    # highlight rolloff.  Blue channel compressed more in highlights than R/G,
    # producing Portra's characteristic warm, creamy highlights.
    # Saturation: ~80 % of digital (negative film is inherently less punchy).
    f['portra'] = dict(
        r=_cspline_lut([(0,18),(16,38),(48,72),(96,128),(144,168),
                        (192,208),(224,228),(240,238),(255,248)]),
        g=_cspline_lut([(0,12),(16,32),(48,65),(96,120),(144,158),
                        (192,200),(224,222),(240,234),(255,244)]),
        b=_cspline_lut([(0,8), (16,24),(48,54),(96,100),(144,132),
                        (192,165),(224,188),(240,205),(255,220)]),
        sat=0.80,
    )

    # ── Fujifilm Velvia 50 ───────────────────────────────────────────────────
    # E-6 reversal (slide).  True black point (no shadow lift), deep S-curve,
    # extreme saturation.  Green/blue channels get slightly more lift than red
    # in the shadows, giving Velvia's vivid greens and rich blues.
    f['velvia'] = dict(
        r=_cspline_lut([(0,0),(32,16),(64,48),(96,102),(128,148),
                        (160,200),(192,232),(224,248),(255,255)]),
        g=_cspline_lut([(0,0),(32,18),(64,52),(96,108),(128,155),
                        (160,205),(192,236),(224,250),(255,255)]),
        b=_cspline_lut([(0,0),(32,20),(64,56),(96,114),(128,160),
                        (160,210),(192,238),(224,252),(255,255)]),
        sat=1.75,
    )

    # ── Ilford HP5 Plus ──────────────────────────────────────────────────────
    # Panchromatic B&W.  Spectral sensitivity: slightly red-biased relative to
    # the standard BT.601 luma formula (R 0.27 vs 0.299, higher G 0.60).
    # Gentle S-curve with good shadow detail and open highlights.
    f['hp5'] = dict(
        bw=True,
        weights=(0.27, 0.60, 0.13),   # (r, g, b)
        curve=_cspline_lut([(0,5),(32,38),(64,74),(96,112),(128,148),
                            (160,185),(192,216),(224,240),(255,252)]),
    )

    # ── Cinestill 800T ───────────────────────────────────────────────────────
    # Kodak Vision3 500T motion-picture stock re-spooled for C-41 processing.
    # Tungsten-balanced: red channel significantly attenuated, blue strongly
    # boosted throughout.  Cyan/teal shadow signature comes from the blue lift
    # combined with the red compression.
    f['cinestill'] = dict(
        r=_cspline_lut([(0,5),(32,28),(64,58),(96,92),(128,126),
                        (160,158),(192,190),(224,218),(255,242)]),
        g=_cspline_lut([(0,8),(32,44),(64,82),(96,118),(128,152),
                        (160,182),(192,210),(224,232),(255,250)]),
        b=_cspline_lut([(0,30),(32,75),(64,118),(96,155),(128,182),
                        (160,208),(192,228),(224,244),(255,255)]),
        sat=0.88,
    )

    # ── Kodak Tri-X 400 ──────────────────────────────────────────────────────
    # High-contrast panchromatic B&W.  Stronger toe than HP5 (shadows go dark
    # quickly), punchy midtones, slight highlight rolloff.  Red-sensitive
    # (R 0.30 vs BT.601's 0.299) giving classic photojournalism rendering.
    f['trix'] = dict(
        bw=True,
        weights=(0.30, 0.59, 0.11),   # (r, g, b)
        curve=_cspline_lut([(0,0),(32,20),(64,50),(96,104),(128,152),
                            (160,200),(192,228),(224,246),(255,255)]),
    )

    # ── Fujifilm Provia 100F ─────────────────────────────────────────────────
    # Professional E-6 slide, renowned for colour accuracy.  Very neutral
    # palette with a slight cool bias; blues and cyans have a subtle lift
    # relative to red.  Moderate saturation boost.
    f['provia'] = dict(
        r=_cspline_lut([(0,0),(32,28),(64,60),(96,96),(128,132),
                        (160,168),(192,200),(224,232),(255,254)]),
        g=_cspline_lut([(0,0),(32,29),(64,62),(96,98),(128,134),
                        (160,170),(192,202),(224,234),(255,255)]),
        b=_cspline_lut([(0,0),(32,32),(64,66),(96,104),(128,140),
                        (160,176),(192,208),(224,236),(255,255)]),
        sat=1.08,
    )

    # ── Kodak Ektar 100 ──────────────────────────────────────────────────────
    # Kodak's most saturated C-41 film.  Vivid, punchy reds are the signature;
    # red channel gets the most aggressive boost.  Shadows are deep but not
    # clipped (small toe lift ~2–4 levels).
    f['ektar'] = dict(
        r=_cspline_lut([(0,4),(32,36),(64,78),(96,126),(128,166),
                        (160,204),(192,230),(224,246),(255,254)]),
        g=_cspline_lut([(0,2),(32,28),(64,64),(96,104),(128,142),
                        (160,178),(192,208),(224,232),(255,252)]),
        b=_cspline_lut([(0,2),(32,26),(64,60),(96,98),(128,134),
                        (160,168),(192,198),(224,226),(255,248)]),
        sat=1.60,
    )

    # ── Agfa Vista 200 ───────────────────────────────────────────────────────
    # Consumer C-41, discontinued.  Strong warm/yellow cast, lifted shadows,
    # blue channel compressed significantly — giving the characteristic faded
    # vintage look with warm highlights.
    f['agfa'] = dict(
        r=_cspline_lut([(0,24),(32,54),(64,90),(96,126),(128,158),
                        (160,190),(192,214),(224,234),(255,248)]),
        g=_cspline_lut([(0,18),(32,50),(64,88),(96,126),(128,162),
                        (160,196),(192,222),(224,240),(255,252)]),
        b=_cspline_lut([(0,8),(32,26),(64,48),(96,74),(128,100),
                        (160,128),(192,156),(224,182),(255,208)]),
        sat=1.12,
    )

    return f


FILM_FILTERS = _build_film_luts()
