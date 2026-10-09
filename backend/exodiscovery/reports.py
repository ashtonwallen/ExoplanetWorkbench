import base64
import csv
import html
import io
import json
import zipfile
from pathlib import Path

from . import store, pipeline


def report(ident):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    r = store.investigation(ident)
    esc = lambda value: html.escape(str(value))
    chunks = [f"<!doctype html><html lang='en'><meta charset='utf-8'><title>{esc(r['target'])} investigation</title><style>body{{font:15px system-ui;max-width:1100px;margin:40px auto;padding:20px;color:#17212b}}h1,h2{{letter-spacing:-.03em}}table{{border-collapse:collapse;width:100%;margin:20px 0}}td,th{{border:1px solid #ddd;text-align:left;padding:9px}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f3f5f7;padding:20px;font-size:11px}}img{{max-width:100%}}.note{{border-left:4px solid #b37618;padding:12px;background:#fff8e9}}@media print{{body{{margin:0}}h2{{break-after:avoid}}img{{break-inside:avoid}}}}</style><body>",
              f"<h1>Exoplanet Search Workbench / {esc(r['target'])}</h1><p>Investigation {ident} · {esc(r['created'])}</p>",
              "<p class='note'>Exploratory transit investigation. No new planet is confirmed or statistically validated by this report. All times are BJD_TDB, not UTC calendar dates.</p>",
              f"<p>{r['cadences']:,} usable cadences; {r['baseline_days']:.3f} day span; {len(r['provenance'])} observations.</p>"]
    for s in r["signals"]:
        chunks.append(f"<h2>Signal {s['index']+1}: {esc(s['classification'])}</h2><table><tr><th>Period (days)</th><th>Epoch (BJD_TDB)</th><th>Box depth (ppm)</th><th>Duration (h)</th><th>Formal depth S/N</th></tr><tr><td>{s['period']:.8f}</td><td>{s['epoch_bjd']:.7f}</td><td>{s['depth_ppm']:.1f} ± {s['depth_error_ppm']:.1f}</td><td>{s['duration_hours']:.3f}</td><td>{s['depth_snr']:.2f}</td></tr></table>")
        plot = pipeline.plots(ident, s["index"])
        fig, axes = plt.subplots(3, 1, figsize=(11, 9), constrained_layout=True)
        axes[0].scatter([x-2457000 for x in plot["time"]], plot["flux"], s=1, c="#237f91", rasterized=True)
        axes[0].set(xlabel="BJD_TDB − 2457000 (days)", ylabel="Detrended relative flux")
        axes[1].plot(plot["period"], plot["power"], lw=.6, c="#237f91")
        axes[1].axvline(s["period"], color="#bc7825", lw=.8)
        axes[1].set(xlabel="Trial period (days)", ylabel="BLS log likelihood")
        axes[2].scatter([x*24 for x in plot["phase"]], plot["fold_flux"], s=1, alpha=.2, c="#237f91")
        axes[2].errorbar([x*24 for x in plot["binned"]["phase"]], plot["binned"]["flux"], yerr=plot["binned"]["error"], fmt=".", color="#183b46", ms=4)
        if s["fit"].get("flux"):
            axes[2].plot([x*24 for x in s["fit"]["phase_days"]], s["fit"]["flux"], c="#bc7825")
        axes[2].set(xlim=(-2*s["duration_hours"], 2*s["duration_hours"]), xlabel="Hours from BLS mid-transit", ylabel="Relative flux")
        buf = io.BytesIO()
        fig.savefig(buf, format="png", dpi=140)
        plt.close(fig)
        chunks.append(f"<img alt='Light curve, BLS periodogram and folded transit for signal {s['index']+1}' src='data:image/png;base64,{base64.b64encode(buf.getvalue()).decode()}'>")
        chunks.append(f"<p>{esc(s['uncertainties'])}</p><p>{esc(s['radius_assumption'])}</p><table><tr><th>Diagnostic</th><th>Status</th><th>Evidence / limitations</th></tr>")
        for test in s["validation"]["tests"]:
            chunks.append(f"<tr><td>{esc(test['name'])}</td><td>{esc(test['status'])}</td><td>{esc(test['detail'])}</td></tr>")
        chunks.append("</table>")
        chunks.append("<h3>Fit assumptions</h3><p>"+esc(s["fit"].get("assumptions", s["fit"]["status"]))+"</p>")
        chunks.append("<h3>Catalog associations</h3><pre>"+esc(json.dumps(s["catalog_matches"], indent=2))+"</pre>")
    chunks.append("<h2>Unresolved alternatives and limitations</h2><ul>"+"".join("<li>"+esc(x)+"</li>" for x in r["limitations"])+"</ul>")
    chunks.append("<h2>Original data products</h2><ul>")
    for p in r["provenance"]:
        chunks.append(f"<li><a href='{esc(p['url'])}'>{esc(p['filename'])}</a><br>SHA256: {esc(p['sha256'])}</li>")
    chunks.append("</ul><h2>Complete numerical record and reproducibility metadata</h2><pre>"+esc(json.dumps(r, indent=2, ensure_ascii=False))+"</pre></body></html>")
    return "".join(chunks)


def bundle(ident):
    r = store.investigation(ident)
    folder = store.DATA / "artifacts" / ident
    output = folder / "investigation.zip"
    a = pipeline.load_arrays(ident)
    csv_buf = io.StringIO()
    writer = csv.writer(csv_buf)
    names = ["time", "flux", "flux_error", "sector", "normalized", "processed", "error", "trend"]
    writer.writerow(names)
    writer.writerows(zip(*(a[n] for n in names)))
    code = """from pathlib import Path
import json, numpy as np
from exodiscovery.schemas import Analysis
from exodiscovery.science import search_bls, preprocess, phase
r = json.loads(Path('result.json').read_text(encoding='utf-8'))
a = dict(np.load('lightcurve.npz'))
cfg = Analysis(**r['config'])
# Recreate both detrending passes from archived quality-filtered raw flux.
y,e,_,_ = preprocess(a['time'],a['flux'],a['flux_error'],a['sector'],cfg.detrend,cfg.window_days)
first,_ = search_bls(a['time'],y,e,cfg)
mask = np.abs(phase(a['time'],first['period'],first['epoch_bjd'])) < first['duration_hours']/24
if cfg.detrend != 'none':
    y,e,_,_ = preprocess(a['time'],a['flux'],a['flux_error'],a['sector'],cfg.detrend,cfg.window_days,mask)
excluded = np.zeros(len(y),bool)
for saved in r['signals']:
    use = ~excluded
    recovered,_ = search_bls(a['time'][use],y[use],e[use],cfg)
    print('Period:',recovered['period'],'Saved:',saved['period'])
    np.testing.assert_allclose(recovered['period'],saved['period'],rtol=1e-7)
    excluded |= np.abs(phase(a['time'],recovered['period'],recovered['epoch_bjd'])) < recovered['duration_hours']/24
    for residual in saved.get('same_period_residuals',[]):
        excluded |= np.abs(phase(a['time'],residual['period'],residual['epoch_bjd'])) < residual['duration_hours']/24
"""
    notebook = {"nbformat": 4, "nbformat_minor": 5, "metadata": {"kernelspec": {"name": "python3", "display_name": "Python 3", "language": "python"}},
                "cells": [{"cell_type": "markdown", "metadata": {}, "source": ["# Reproduce the saved BLS searches\nInstall the included code and pinned environment, then run from this extracted directory. Raw FITS and full processed arrays are included. See README.txt."]},
                          {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": code.splitlines(True)}]}
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("report.html", report(ident))
        z.writestr("result.json", json.dumps(r, indent=2))
        z.writestr("lightcurve.csv", csv_buf.getvalue())
        z.writestr("reproduce.py", code)
        z.writestr("reproduce.ipynb", json.dumps(notebook, indent=2))
        z.writestr("requirements-science.txt", "\n".join(f"{k}=={v}" for k,v in r["software"].items() if k != "exodiscovery"))
        z.writestr("README.txt", "Create a Python virtual environment. pip install -r requirements-science.txt\npip install ./source\npython reproduce.py\nRun from this extracted directory. All numerical arrays are full resolution. FITS checksums and original archive URLs are in result.json. The included source snapshot is the analysis-time code when available (otherwise the export-time version). Verify it against source_hashes in result.json.\n")
        for p in folder.glob("*.npz"):
            z.write(p, p.name)
        for p in r["provenance"]:
            z.write(store.DATA / "observations" / p["local_file"], "observations/"+p["local_file"])
        included = {p['local_file'] for p in r['provenance']}
        for s in r['signals']:
            for f in s.get('followups',[]):
                provenance = f['result'].get('provenance')
                if provenance and provenance.get('local_file') not in included:
                    z.write(store.DATA/'observations'/provenance['local_file'],'observations/'+provenance['local_file'])
                    included.add(provenance['local_file'])
                snapshot = store.DATA/'artifacts'/f['job_id']/'source'
                if snapshot.exists():
                    for p in snapshot.rglob('*'):
                        if p.is_file() and '__pycache__' not in p.parts:
                            z.write(p,'followup-source/'+f['job_id']+'/'+p.relative_to(snapshot).as_posix())
        snapshot = folder / 'source'
        root = snapshot if snapshot.exists() else Path(__file__).resolve().parents[2]
        z.write(root / "pyproject.toml", "source/pyproject.toml")
        for p in (root/'backend'/'exodiscovery').glob("*.py"):
            z.write(p, "source/backend/exodiscovery/"+p.name)
    return output
