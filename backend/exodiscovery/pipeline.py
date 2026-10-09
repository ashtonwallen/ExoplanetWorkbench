import json
import hashlib
import shutil
import sys
from pathlib import Path

import numpy as np

from . import archive, science, store
from .schemas import Analysis, Injection, CandidateRef, Alias


class Cancelled(Exception):
    pass


def checkpoint(job_id, text, progress=None):
    if job_id:
        job = store.get_job(job_id)
        if job and job["cancel"]:
            raise Cancelled()
        store.event(job_id, text, progress)


def atomic_json(path, payload):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(science.clean_json(payload), indent=2, allow_nan=False), encoding="utf-8")
    tmp.replace(path)


def analyze(config: Analysis, job_id=None, artifact_id=None):
    ident = artifact_id or job_id or store.uid()
    folder = store.DATA / "artifacts" / ident
    folder.mkdir(parents=True, exist_ok=True)
    if (folder / "result.json").exists():
        result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
        store.save_investigation(result, ident)
        return result
    source = folder / 'source'
    (source / 'backend' / 'exodiscovery').mkdir(parents=True, exist_ok=True)
    for p in Path(__file__).parent.glob('*.py'):
        shutil.copy2(p, source / 'backend' / 'exodiscovery' / p.name)
    shutil.copy2(Path(__file__).resolve().parents[2] / 'pyproject.toml', source / 'pyproject.toml')
    checkpoint(job_id, f"Searching {config.mission}: {config.target}", .05)
    found = archive.search(config.target, config.mission)
    products = archive.select_products(found, config.sectors, config.max_products)
    if not products:
        raise ValueError("No calibrated light curves found for the selected target/sectors. TESS currently uses SPOC products; FFI extraction is not included.")
    arrays, provenance = [], []
    remaining = config.max_download_mb * 1024**2
    for i, product in enumerate(products):
        checkpoint(job_id, f"Loading observation {i+1}/{len(products)}", .1 + .25*i/len(products))
        path, meta = archive.download(product, remaining, lambda s: checkpoint(job_id, s))
        # Budget covers total selected product bytes, including cached inputs.
        remaining -= meta["bytes"]
        if remaining < 0:
            raise ValueError("Selected observations exceed the data budget, including cached inputs")
        t, f, e, sectors, header = archive.read_lightcurve(path, config.quality)
        arrays.append((t, f, e, sectors))
        provenance.append({**meta, **header})
    t, f, e, sectors = [np.concatenate([a[i] for a in arrays]) for i in range(4)]
    if len({p['tic_id'] for p in provenance if p.get('tic_id') is not None}) > 1:
        raise ValueError('Archive returned observations of different TIC targets; refusing to combine them')
    order = np.argsort(t)
    t, f, e, sectors = [v[order] for v in [t, f, e, sectors]]
    _, unique = np.unique(t, return_index=True)
    t, f, e, sectors = [v[unique] for v in [t, f, e, sectors]]
    if len(t) < 200:
        raise ValueError("Fewer than 200 usable quality-filtered cadences")
    checkpoint(job_id, "Normalizing and detrending continuous segments", .38)
    y, err, trend, normalized = science.preprocess(t, f, e, sectors, config.detrend, config.window_days)
    star = {k: provenance[0].get(k) for k in ["tic_id", "ra", "dec", "stellar_radius_solar", "teff_k", "tmag"]}
    checkpoint(job_id, "Cross-referencing NASA Exoplanet Archive and TOI catalog", .42)
    cat = archive.catalogs(star["ra"], star["dec"], star["tic_id"])
    np.savez_compressed(folder / "lightcurve.npz", time=t, flux=f, flux_error=e, sector=sectors,
                        normalized=normalized, processed=y, error=err, trend=trend)
    atomic_json(folder / "inputs.json", {"config": config.model_dump(), "provenance": provenance, "catalogs": cat})
    signals = []
    excluded = np.zeros(len(t), dtype=bool)
    for attempt in range(config.max_signals + 3):
        k = len(signals)
        if k >= config.max_signals:
            break
        checkpoint(job_id, f"BLS search {k+1}/{config.max_signals}", .47 + .35*k/config.max_signals)
        use = ~excluded
        if use.sum() < 200:
            break
        signal, periodogram = science.search_bls(t[use], y[use], err[use], config)
        # Second pass protects the strongest initial event while estimating the trend.
        mask = np.abs(science.phase(t, signal["period"], signal["epoch_bjd"])) < signal["duration_hours"] / 24
        if k == 0 and config.detrend != "none":
            y, err, trend, normalized = science.preprocess(t, f, e, sectors, config.detrend, config.window_days, mask)
            signal, periodogram = science.search_bls(t, y, err, config)
            np.savez_compressed(folder / "lightcurve.npz", time=t, flux=f, flux_error=e, sector=sectors,
                                normalized=normalized, processed=y, error=err, trend=trend)
        # A hot planet's secondary eclipse is not an additional planet. Preserve it
        # as a same-period residual, mask it, and continue the multi-signal search.
        parent = next((s for s in signals if abs(s["period"]/signal["period"]-1) < .005), None)
        if parent is not None:
            parent.setdefault("same_period_residuals", []).append({"period": signal["period"], "epoch_bjd": signal["epoch_bjd"],
                "duration_hours": signal["duration_hours"], "depth_ppm": signal["depth_ppm"],
                "interpretation": "Residual at an existing orbital period; possible secondary eclipse or imperfect masking, not counted as an additional candidate."})
            excluded |= np.abs(science.phase(t, signal["period"], signal["epoch_bjd"])) < signal["duration_hours"] / 24
            continue
        signal["index"] = k
        signal["masked_previous_signals"] = list(range(k))
        signal["validation"] = science.diagnostics(t[use], y[use], err[use], sectors[use], signal)
        checkpoint(job_id, f"Fitting transit shape and testing preprocessing for signal {k+1}")
        signal["fit"] = science.fit_transit(t[use], y[use], err[use], signal)
        signal["catalog_matches"] = science.crossmatch(signal, cat, star["ra"], star["dec"], star["tic_id"])
        signal["radius_earth_estimate"] = None
        radius = star.get("stellar_radius_solar")
        if radius and radius > 0:
            signal["radius_earth_estimate"] = float(np.sqrt(max(signal["depth"], 0))*radius*109.076)
        signal["radius_assumption"] = "R_p/R_earth = sqrt(BLS depth) × FITS catalog R_star/R_sun × 109.076; target host assumed, no additional dilution or limb-darkening correction. Catalog radius uncertainty unavailable; not a precise radius measurement."
        variants = []
        for method in ["median", "savgol", "none"]:
            vy, ve, _, _ = science.preprocess(t, f, e, sectors, method, config.window_days, mask)
            inside = np.abs(science.phase(t[use], signal["period"], signal["epoch_bjd"])) < signal["duration_hours"] / 48
            variants.append({"method": method, **science.depth_measure(vy[use], ve[use], inside)})
        signal["preprocessing_checks"] = variants
        med, sg = variants[0], variants[1]
        unstable = med['snr'] is None or sg['snr'] is None or min(med['snr'],sg['snr']) < 5 or abs(med['depth_ppm']-sg['depth_ppm']) > max(abs(med['depth_ppm']),abs(sg['depth_ppm']))*.5
        signal["validation"]["tests"].append({"name": "Preprocessing sensitivity", "status": "flag" if unstable else "measured",
            "detail": "Fixed-ephemeris depths from median, Savitzky–Golay, and normalization only. Flagged if either detrended S/N <5 or their depths differ by >50%; a heuristic sensitivity check, not independent blind recovery."})
        transit_mask = np.abs(science.phase(t[use],signal['period'],signal['epoch_bjd'])) < signal['duration_hours']/48
        edge = np.zeros(use.sum(),bool)
        used_t, used_sector = t[use], sectors[use]
        for sec in np.unique(used_sector):
            idx = np.flatnonzero(used_sector==sec)
            for segment in np.split(idx,np.flatnonzero(np.diff(used_t[idx])>.3)+1):
                edge[segment] = (used_t[segment]-used_t[segment].min()<config.window_days/2) | (used_t[segment].max()-used_t[segment]<config.window_days/2)
        edge_fraction = float((transit_mask & edge).sum()/max(1,transit_mask.sum()))
        signal['validation']['tests'].append({'name':'Data-gap proximity','status':'flag' if edge_fraction>.5 else 'measured',
            'detail':f'{edge_fraction:.1%} of in-transit samples lie within half a trend window of segment edges (>0.3-day gaps); edge-dominated signals need reanalysis.'})
        boundary = signal['duration_hours'] >= config.max_duration*.98 or signal['duration_hours'] <= config.min_duration*1.02 or min(abs(signal['period']/config.min_period-1),abs(signal['period']/config.max_period-1))<.002
        signal['validation']['tests'].append({'name':'Search boundary','status':'flag' if boundary else 'pass',
            'detail':'Period or duration lies at the search boundary; broaden the range before interpretation.' if boundary else 'Selected period and duration lie within the search range.'})
        signal['classification'] = science.classify(signal,cat)
        signal["uncertainties"] = "BLS depth error assumes independent residuals. Grid spacing is resolution, not period uncertainty. Period/epoch posterior uncertainties, red-noise calibration, and survey false-alarm rate have not been computed."
        if signal["grid_capped"]:
            signal["validation"]["tests"].append({"name": "Period-grid completeness", "status": "flag", "detail": "150,000-point resource cap reached. Long-baseline narrow peaks can be missed; narrow the period range or analyze sectors separately."})
        np.savez_compressed(folder / f"periodogram_{k}.npz", period=periodogram[0], power=periodogram[1])
        signals.append(science.clean_json(signal))
        excluded |= np.abs(science.phase(t, signal["period"], signal["epoch_bjd"])) < signal["duration_hours"] / 24
        if signal["depth_snr"] < 5:
            break
    result = science.clean_json({"id": ident, "target": config.target, "created": store.now(), "star": star,
              "config": config.model_dump(), "provenance": provenance, "catalogs": cat, "signals": signals,
              "cadences": len(t), "baseline_days": float(np.ptp(t)), "software": science.versions(),
              "source_hashes": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (source/'backend'/'exodiscovery').glob('*.py')},
              "python_version": sys.version,
              "history": [{"time": store.now(), "action": "Calibrated photometry downloaded, quality filtered, detrended, BLS searched and diagnosed."}],
              "limitations": ["Transit-like signals are not confirmed discoveries.",
                 "Catalog searches cover NASA confirmed planets and TOIs within 30 arcsec, not exhaustive literature or community candidates.",
                 "ExoFOP is linked for manual follow-up review; its observation tables have not been ingested.",
                 "Formal S/N is not a false-alarm probability; correlated noise and search trials are not calibrated.",
                 "Combined-sector consistency is not a held-out-year prediction test.",
                 "FITS stellar parameters may be outdated; radius estimates assume the target hosts the transit."]})
    atomic_json(folder / "result.json", result)
    store.save_investigation(result, ident)
    checkpoint(job_id, "Investigation saved", 1.)
    return result


def load_arrays(ident):
    return dict(np.load(store.DATA / "artifacts" / ident / "lightcurve.npz"))


def summary(result):
    return {"id": result["id"], "target": result["target"], "star": result["star"], "cadences": result["cadences"],
            "signals": [{k: s.get(k) for k in ["index", "period", "depth_ppm", "depth_snr", "classification", "validation", "catalog_matches", "uncertainties"]} for s in result["signals"]],
            "limitations": result["limitations"], "report_url": f"/api/investigations/{result['id']}/report"}


def plots(ident, index=0):
    r = store.investigation(ident)
    s = r["signals"][index]
    a = load_arrays(ident)
    t, y, e = a["time"], a["processed"], a["error"]
    fold_keep = np.ones(len(t),bool)
    for previous in r['signals'][:index]:
        for masked in [previous,*previous.get('same_period_residuals',[])]:
            fold_keep &= np.abs(science.phase(t,masked['period'],masked['epoch_bjd'])) >= masked['duration_hours']/24
    rng = np.random.default_rng(42)
    # Plot-only sampling preserves the transit neighborhood and displays the count.
    transit = np.abs(science.phase(t, s["period"], s["epoch_bjd"])) < s["duration_hours"] / 24
    chosen = np.unique(np.concatenate([np.flatnonzero(transit)[::max(1, int(transit.sum()/5000))],
                                      rng.choice(len(t), min(12000, len(t)), replace=False)]))
    pg = dict(np.load(store.DATA / "artifacts" / ident / f"periodogram_{index}.npz"))
    # Keep the maximum of each period bin, so narrow peaks remain visible.
    chunks = np.array_split(np.arange(len(pg["period"])), min(5000, len(pg["period"])))
    peaks = np.array([c[np.argmax(pg["power"][c])] for c in chunks])
    return science.clean_json({"time": t[chosen], "normalized": a["normalized"][chosen], "flux": y[chosen],
            "trend": a["trend"][chosen], "sector": a["sector"][chosen], "error": e[chosen],
            "phase": science.phase(t[chosen][fold_keep[chosen]], s["period"], s["epoch_bjd"]),
            "fold_flux": y[chosen][fold_keep[chosen]],
            "binned": science.bin_phase(t[fold_keep], y[fold_keep], e[fold_keep], s["period"], s["epoch_bjd"], max(160, min(1500, int(s["period"]/(s["duration_hours"]/24)*12)))),
            "period": pg["period"][peaks], "power": pg["power"][peaks], "total_points": len(t), "shown_points": len(chosen)})


def followup(params, kind, job_id, operation_id=None):
    ref = Injection(**params) if kind == "injection" else Alias(**params) if kind == "alias" else CandidateRef(**params)
    r = store.investigation(ref.investigation_id)
    s = r["signals"][ref.signal_index]
    if operation_id:
        prior = next((f for f in s.get('followups',[]) if f.get('operation_id')==operation_id and f['job_id']==job_id),None)
        if prior:
            return prior
    a = load_arrays(r["id"])
    checkpoint(job_id, f"Running {kind} analysis", .1)
    if kind == "gaia":
        result = archive.gaia(r["star"]["ra"], r["star"]["dec"])
    elif kind == "alias":
        config = Analysis(**r["config"])
        config.min_period, config.max_period = ref.period*.99, ref.period*1.01
        config.max_duration = min(config.max_duration, config.min_period*24*.8)
        signal, _ = science.search_bls(a["time"], a["processed"], a["error"], config,
                                     np.linspace(config.min_period, config.max_period, 1201))
        result = {"search": signal, "validation": science.diagnostics(a["time"], a["processed"], a["error"], a["sector"], signal)}
    elif kind == "injection":
        config = Analysis(**r["config"])
        if not config.min_period <= ref.period <= config.max_period:
            raise ValueError("Injection period must lie within the saved search range")
        rng = np.random.default_rng(ref.seed)
        # Mask all previously found signals to estimate residual sensitivity.
        keep = np.ones(len(a["time"]), bool)
        for existing in r["signals"]:
            keep &= np.abs(science.phase(a["time"], existing["period"], existing["epoch_bjd"])) > existing["duration_hours"] / 24
        t, f, e, sectors = [a[k][keep] for k in ["time", "flux", "flux_error", "sector"]]
        duration = min(s["duration_hours"]/24, ref.period*.08)
        trials = []
        for i in range(ref.trials):
            checkpoint(job_id, f"Injection recovery {i+1}/{ref.trials}", .1+.8*i/ref.trials)
            epoch = float(t.min()+rng.uniform(0, ref.period))
            injected = f.copy()
            injected[np.abs(science.phase(t, ref.period, epoch)) < duration/2] *= 1-ref.depth_ppm/1e6
            y, er, _, _ = science.preprocess(t, injected, e, sectors, config.detrend, config.window_days)
            recovered, _ = science.search_bls(t, y, er, config)
            match = abs(recovered["period"]/ref.period-1) < .01
            phase_match = abs(float(science.phase([recovered["epoch_bjd"]], ref.period, epoch)[0])) < duration
            trials.append({"epoch": epoch, "recovered_period": recovered["period"], "snr": recovered["depth_snr"],
                           "recovered": bool(match and phase_match and recovered["depth_snr"] >= 7)})
        count = sum(t["recovered"] for t in trials)
        result = {"trials": trials, "recovered": count, "total": ref.trials, "fraction": count/ref.trials,
                  "parameters": ref.model_dump(), "duration_hours": duration*24,
                  "caveat": "Box injections into quality-filtered PDCSAP before detrending, random phase, previous signals masked. Conditional sensitivity at one period/depth, not survey completeness or a false-positive probability."}
    elif kind == "pixels":
        from .pixels import inspect_pixels
        result = inspect_pixels(r, s, job_id)
    else:
        raise ValueError("Unknown follow-up")
    record = {"kind": kind, "time": store.now(), "job_id": job_id, "operation_id":operation_id, "result": science.clean_json(result),
              'parameters':ref.model_dump(),'software':science.versions(),
              'source_hashes':{p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in Path(__file__).parent.glob('*.py')}}
    snapshot = store.DATA/'artifacts'/job_id/'source'/'backend'/'exodiscovery'
    snapshot.mkdir(parents=True,exist_ok=True)
    for p in Path(__file__).parent.glob('*.py'):
        shutil.copy2(p,snapshot/p.name)
    shutil.copy2(Path(__file__).resolve().parents[2]/'pyproject.toml',snapshot.parents[1]/'pyproject.toml')
    s.setdefault("followups", []).append(record)
    test = None
    if kind == 'pixels' and result.get('status') == 'measured':
        test = {'name':'Source localization','status':'inconclusive',
                'detail':'Exploratory difference image and two aperture depths measured. Calibrated PRF fitting, pointing corrections and centroid uncertainty are still required to establish the host.'}
    elif kind == 'gaia':
        test = {'name':'Nearby-source catalog','status':'measured',
                'detail':f"{len(result['rows'])} Gaia DR3 sources returned within 120 arcsec. Proper-motion propagation, TESS-band dilution and unresolved companions remain untested."}
    elif kind == 'injection':
        test = {'name':'Injection recovery','status':'measured',
                'detail':f"{result['recovered']}/{result['total']} box injections recovered at {ref.period:g} days and {ref.depth_ppm:g} ppm. Conditional sensitivity, not false-positive probability or survey completeness."}
    if test:
        s['validation']['tests'] = [t for t in s['validation']['tests'] if t['name'] != test['name']] + [test]
    r["history"].append({"time": store.now(), "action": f"{kind} analysis on signal {ref.signal_index+1}", "job_id": job_id})
    store.save_investigation(r, r["id"])
    atomic_json(store.DATA / "artifacts" / r["id"] / "result.json", r)
    checkpoint(job_id, f"{kind} result saved", 1.)
    return record
