"""Deterministic transit search and diagnostic tests; never a planet validator."""
import importlib.metadata
import math

import numpy as np
from astropy.timeseries import BoxLeastSquares
from scipy.ndimage import median_filter
from scipy.signal import savgol_filter
from scipy.optimize import least_squares


def clean_json(x):
    if isinstance(x, dict):
        return {str(k): clean_json(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, np.ndarray)):
        return [clean_json(v) for v in x]
    if isinstance(x, np.generic):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    return x


def versions():
    return {p: importlib.metadata.version(p) for p in ["exodiscovery", "numpy", "scipy", "astropy", "lightkurve", "astroquery", "batman-package"]}


def phase(t, period, epoch):
    return (np.asarray(t) - epoch + period / 2) % period - period / 2


def robust_sigma(y):
    return max(float(1.4826 * np.median(np.abs(y - np.median(y)))), 1e-8)


def preprocess(t, f, e, sectors, method="median", window_days=1.5, mask=None):
    """Independent normalization per sector and continuous segment; no lower clipping.

    Gaps >0.3 day break the trend. A transit mask interpolates only the trend input,
    not the observations. Returned errors retain original units divided by trend.
    """
    y, err, trend, normalized = [np.zeros_like(f, dtype=float) for _ in range(4)]
    mask = np.zeros(len(t), dtype=bool) if mask is None else mask
    for s in np.unique(sectors):
        idx = np.flatnonzero(sectors == s)
        norm = np.median(f[idx])
        normalized[idx] = f[idx] / norm
        boundaries = np.flatnonzero(np.diff(t[idx]) > 0.3) + 1
        for segment in np.split(idx, boundaries):
            yy = f[segment] / norm
            cadence = np.median(np.diff(t[segment])) if len(segment) > 1 else window_days
            w = max(5, int(window_days / max(cadence, 1e-6)) | 1)
            w = min(w, len(segment) if len(segment) % 2 else len(segment) - 1)
            if method == "none" or len(segment) < 7:
                tr = np.ones(len(segment))
            else:
                keep = ~mask[segment]
                filled = np.interp(t[segment], t[segment][keep], yy[keep]) if keep.sum() > 3 else yy
                tr = median_filter(filled, size=w, mode="nearest")
                if method == "savgol":
                    # Clip trend input only; observed negative flux deviations are never clipped.
                    scale = robust_sigma(filled - tr)
                    filled = np.clip(filled, tr - 3 * scale, tr + 3 * scale)
                    tr = savgol_filter(filled, w, 2)
            tr = np.maximum(tr, 0.01)
            trend[segment] = tr
            y[segment] = yy / tr
            err[segment] = e[segment] / norm / tr
    # PDCSAP errors do not include all residual stellar/instrumental variability.
    empirical = robust_sigma(np.diff(y)) / np.sqrt(2)
    err *= max(1.0, empirical / np.median(err))
    return y, err, trend, normalized


def search_bls(t, y, e, config, periods=None):
    offset = float(t.min())
    model = BoxLeastSquares(t - offset, y, e)
    durations = np.geomspace(config.min_duration / 24, config.max_duration / 24, 9)
    capped = False
    if periods is None:
        expected = 1 + (1/config.min_period - 1/config.max_period) * np.ptp(t)**2 / (2*durations.min())
        # Check before autoperiod allocates its array: year-separated sectors can
        # otherwise require hundreds of millions of trial periods.
        if expected > 150000:
            periods = np.reciprocal(np.linspace(1 / config.max_period, 1 / config.min_period, 150000))[::-1]
            capped = True
        else:
            periods = model.autoperiod(durations, minimum_period=config.min_period,
                                       maximum_period=config.max_period, frequency_factor=2)
    result = model.power(periods, durations, objective="likelihood", oversample=10)
    best = int(np.argmax(result.power))
    period = float(result.period[best])
    # Refine the peak locally using full baseline after the broad search.
    spacing = float(np.median(np.abs(np.diff(periods[max(0, best-2):best+3])))) if len(periods) > 1 else period * 0.001
    fine = np.linspace(max(config.min_period, period-2*spacing), min(config.max_period, period+2*spacing), 501)
    refined = model.power(fine, durations, objective="likelihood", oversample=15)
    k = int(np.argmax(refined.power))
    p, d, epoch = float(refined.period[k]), float(refined.duration[k]), float(refined.transit_time[k]) + offset
    depth, depth_error = float(refined.depth[k]), float(refined.depth_err[k])
    stats = model.compute_stats(p, d, epoch - offset)
    peak_indices = np.argsort(result.power)[::-1]
    peaks = []
    for i in peak_indices:
        pp = float(result.period[i])
        if all(abs(pp - v["period"]) / pp > 0.02 for v in peaks):
            peaks.append({"period": pp, "power": float(result.power[i])})
        if len(peaks) == 8:
            break
    return {"period": p, "epoch_bjd": epoch, "duration_hours": d*24, "depth": depth,
            "depth_ppm": depth*1e6, "depth_error_ppm": depth_error*1e6,
            "depth_snr": depth / depth_error, "log_likelihood": float(refined.power[k]),
            "period_grid_step_days": float(abs(fine[1]-fine[0])),
            "grid_capped": capped, "grid_points": len(periods), "alternative_peaks": peaks,
            "bls_statistics": clean_json(stats)}, (result.period, result.power)


def depth_measure(y, e, mask, baseline_mask=None):
    baseline_mask = ~mask if baseline_mask is None else baseline_mask
    if mask.sum() < 3 or baseline_mask.sum() < 5:
        return {"status": "insufficient_data", "depth_ppm": None, "error_ppm": None, "snr": None, "points": int(mask.sum())}
    w, w0 = 1/e[mask]**2, 1/e[baseline_mask]**2
    depth = np.average(y[baseline_mask], weights=w0) - np.average(y[mask], weights=w)
    error = np.sqrt(1/w.sum() + 1/w0.sum())
    return {"status": "measured", "depth_ppm": float(depth*1e6), "error_ppm": float(error*1e6),
            "snr": float(depth/error), "points": int(mask.sum())}


def diagnostics(t, y, e, sectors, signal):
    p, epoch, d = signal["period"], signal["epoch_bjd"], signal["duration_hours"] / 24
    ph = phase(t, p, epoch)
    inside = np.abs(ph) < d/2
    outside = (np.abs(ph) > d) & (np.abs(ph) < min(3*d, p/3))
    cycles = np.rint((t - epoch) / p).astype(int)
    odd = depth_measure(y, e, inside & (cycles % 2 == 1), outside)
    even = depth_measure(y, e, inside & (cycles % 2 == 0), outside)
    odd_even = None
    if odd["depth_ppm"] is not None and even["depth_ppm"] is not None:
        odd_even = abs(odd["depth_ppm"] - even["depth_ppm"]) / np.hypot(odd["error_ppm"], even["error_ppm"])
    secondary = depth_measure(y, e, np.abs(phase(t, p, epoch+p/2)) < d/2,
                              (np.abs(ph) > d) & (np.abs(phase(t, p, epoch+p/2)) > d))
    per_sector = []
    for s in np.unique(sectors):
        use = sectors == s
        per_sector.append({"sector": int(s), **depth_measure(y[use], e[use], inside[use], outside[use]),
                           "start_bjd": float(t[use].min()), "end_bjd": float(t[use].max())})
    events = []
    for n in np.unique(cycles[inside]):
        use = cycles == n
        measurement = depth_measure(y[use], e[use], inside[use], outside[use])
        cadence = np.median(np.diff(t[use])) if use.sum() > 1 else d
        coverage = min(1., float((inside & use).sum() * cadence / d))
        before = int((use & (ph < -d*.75) & (ph > -3*d)).sum())
        after = int((use & (ph > d*.75) & (ph < 3*d)).sum())
        measurement.update(coverage_fraction=coverage, baseline_before=before, baseline_after=after)
        if coverage < .7 or before < 2 or after < 2:
            measurement['status'] = 'incomplete_coverage'
        events.append({"cycle": int(n), "predicted_bjd": float(epoch+n*p), **measurement})
    observed = sum(v["status"] == "measured" for v in events)
    tests = [
        {"name": "Repeated events", "status": "pass" if observed >= 3 else "inconclusive", "detail": f"{observed} events with ≥70% estimated coverage, ≥3 in-transit points, and baseline on both sides."},
        {"name": "Odd / even depths", "status": "inconclusive" if odd_even is None else ("flag" if odd_even > 3 else "pass"),
         "detail": "Alternating depths: " + (f"{odd_even:.2f} formal sigma difference." if odd_even is not None else "not enough samples."), "sigma": odd_even},
        {"name": "Secondary eclipse at phase 0.5", "status": "inconclusive" if secondary["snr"] is None else ("flag" if secondary["snr"] > 5 else "pass"),
         "detail": "Circular-orbit phase only; eccentric eclipses are not excluded.", "measurement": secondary},
        {"name": "Independent sectors", "status": "measured" if len(per_sector) > 1 else "inconclusive",
         "detail": f"{len(per_sector)} sectors measured at the combined ephemeris. This is not a held-out prediction test."},
        {"name": "Source localization", "status": "not_run", "detail": "Pixel difference-image and aperture tests required. Nearby or unresolved stars may host the eclipse."},
        {"name": "Transit timing variation", "status": "not_run", "detail": "Individual event depths are measured; individual transit times have not been fitted."},
        {"name": "Statistical false-positive probability", "status": "not_run", "detail": "No calibrated false-alarm rate or astrophysical false-positive probability computed."},
    ]
    return {"tests": tests, "odd": odd, "even": even, "odd_even_sigma": odd_even,
            "secondary": secondary, "sectors": per_sector, "events": events, "observed_transits": observed}


def fit_transit(t, y, e, signal):
    """Cadence-integrated batman least-squares fit, fixed circular orbit and limb darkening.

    Approximate covariance is explicitly conditional, not a posterior uncertainty.
    """
    import batman
    p, epoch, duration = signal["period"], signal["epoch_bjd"], signal["duration_hours"] / 24
    ph = phase(t, p, epoch)
    use = np.abs(ph) < min(3 * duration, p * .4)
    if use.sum() < 25:
        return {"status": "insufficient_data"}
    cadence = float(np.median(np.diff(t)))
    pars = batman.TransitParams()
    pars.t0, pars.per, pars.rp, pars.a, pars.inc = 0., p, .1, 10., 89.
    pars.ecc, pars.w, pars.u, pars.limb_dark = 0., 90., [.3, .2], "quadratic"
    time = np.ascontiguousarray(ph[use])
    model = batman.TransitModel(pars, time, supersample_factor=7, exp_time=cadence)

    def predict(theta, mod=model):
        rp, ar, b, dt, base = theta
        pars.rp, pars.a = rp, ar
        pars.inc = np.degrees(np.arccos(np.clip(b / ar, 0, 1)))
        pars.t0 = dt
        return base * mod.light_curve(pars)

    start = [np.clip(np.sqrt(max(signal["depth"], 1e-6)), .002, .4), np.clip(p/(np.pi*duration), 2.1, 190), .3, 0., 1.]
    result = least_squares(lambda theta: (predict(theta)-y[use])/e[use], start,
                           bounds=([.001, 1.5, 0., -duration, .95], [.5, 200, 1.4, duration, 1.05]), max_nfev=250)
    rp, ar, b, dt, base = result.x
    errors = None
    if np.linalg.matrix_rank(result.jac) == 5:
        cov = np.linalg.pinv(result.jac.T @ result.jac) * (2*result.cost / max(1, len(time)-5))
        errors = np.sqrt(np.maximum(np.diag(cov), 0)).tolist()
    grid = np.linspace(-2*duration, 2*duration, 400)
    plotted = batman.TransitModel(pars, grid, supersample_factor=7, exp_time=cadence)
    curve = predict(result.x, plotted)
    return clean_json({"status": "fitted" if result.success else "not_converged", "rp_over_rstar": rp,
                       "a_over_rstar": ar, "impact_parameter": b, "epoch_bjd": epoch+dt,
                       "baseline": base, "period_fixed_days": p, "formal_errors": errors,
                       "error_parameter_order": ["rp_over_rstar", "a_over_rstar", "impact_parameter", "epoch_days", "baseline"],
                       "reduced_chi2": 2*result.cost / max(1, len(time)-5),
                       "grazing": bool(b + rp > 1), "phase_days": grid, "flux": curve,
                       "assumptions": "Circular orbit; fixed quadratic limb-darkening [0.3,0.2]; fixed BLS period; no dilution correction beyond PDCSAP; 7-point cadence integration. Local covariance errors are conditional and may be unreliable for grazing/degenerate fits. No posterior sampling."})


def crossmatch(signal, catalog, ra, dec, tic_id):
    matches = []
    for kind in ["confirmed", "toi"]:
        for row in catalog.get("records", {}).get(kind, {}).get("rows", []):
            if not row.get("pl_orbper") or row.get("ra") is None or row.get("dec") is None:
                continue
            distance = 3600*np.hypot((row["ra"]-ra)*np.cos(np.deg2rad(dec)), row["dec"]-dec)
            tid = str(row.get("tid", row.get("tic_id", ""))).replace("TIC", "").strip().split(".")[0]
            same = (tic_id is not None and tid == str(tic_id)) or distance < 3
            ratio = signal["period"] / row["pl_orbper"]
            if same and abs(ratio-1) < .005:
                matches.append({"catalog": kind, "record": row, "separation_arcsec": float(distance), "period_ratio": ratio})
    return matches


def classify(signal, catalog):
    # 'Known' describes a positional/period catalog association, not source validation.
    if signal['depth_snr'] < 7 or signal['validation']['observed_transits'] < 3:
        return 'Insufficient evidence'
    if any(m["catalog"] == "confirmed" for m in signal["catalog_matches"]):
        return "Known planet"
    if signal["catalog_matches"]:
        if any(m['record'].get('tfopwg_disp') in ('FP','FA') for m in signal['catalog_matches']):
            return 'Likely false positive'
        return "Previously cataloged candidate"
    diag = signal["validation"]
    if (diag["odd_even_sigma"] or 0) > 3 or (diag["secondary"]["snr"] or 0) > 5:
        return "Likely false positive"
    if any(v['status']=='flag' for v in diag['tests'] if v['name'] in ['Preprocessing sensitivity','Data-gap proximity','Search boundary']):
        return 'Insufficient evidence'
    complete = all(catalog.get("records", {}).get(k, {}).get("status") == "complete" for k in ["confirmed", "toi"])
    if signal["depth_snr"] < 7 or diag["observed_transits"] < 3 or signal["grid_capped"] or not complete:
        return "Insufficient evidence"
    return "Interesting unclassified transit signal"


def bin_phase(t, y, e, period, epoch, n=160):
    ph = phase(t, period, epoch)
    edges = np.linspace(-period/2, period/2, n+1)
    idx = np.digitize(ph, edges)-1
    out = {"phase": [], "flux": [], "error": [], "count": []}
    for i in range(n):
        use = idx == i
        if not use.any():
            continue
        weights = 1/e[use]**2
        out["phase"].append(float(np.mean(ph[use])))
        out["flux"].append(float(np.average(y[use], weights=weights)))
        out["error"].append(float(np.sqrt(1/weights.sum())))
        out["count"].append(int(use.sum()))
    return out
