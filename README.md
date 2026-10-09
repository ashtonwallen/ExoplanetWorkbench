# Exoplanet Search Workbench

A local astronomical workbench for downloading calibrated observations, finding periodic transit-like signals, and preserving the evidence needed to investigate them. Scientific calculations work without an AI provider.

## Start

Requirements: **Python 3.11–3.13**, **Node.js 20+**, and an internet connection for public archives. Windows is the tested platform.

```powershell
python run.py
```

The launcher creates `.venv`, installs the Python dependencies, builds the frontend if needed, starts the independent worker and API, and opens **http://127.0.0.1:8765**. First setup can take several minutes. Subsequent starts use the installed environment and compiled frontend.

```powershell
python run.py --no-browser       # serve without opening a browser
python run.py --rebuild          # rebuild after frontend changes
python run.py --setup-only       # install/build without starting
```

Keep the launcher running. Ctrl+C stops it; interrupted jobs retain their downloaded products and completed artifacts. Use **Research queue → Resume** after restarting. One launcher may use a data directory at a time. Do not start additional workers against the same database.

## Verify real science

1. Choose **New investigation → WASP-18 · known positive**.
2. Keep Sector **2**, the **0.5–10 day** period range, and the default detrending window.
3. Click **Queue analysis**. The queue shows download, preprocessing, search, and fitting stages.
4. Open the saved investigation. The strongest transit should recover **WASP-18 b**, approximately **0.94145 days**, with a NASA position-and-period association.
5. Inspect **Photometry**, **Transit analysis**, **Evidence**, and **Sources**. Download the report or reproduction ZIP.

The live integration check recovered **0.9414608208 days**, a box depth of approximately **9,436 ppm**, from **18,299** quality-filtered Sector 2 cadences. These are locally measured values, not seeded UI content. The exact search result depends on pipeline version, product selection, and processing settings. The catalog period retrieved during development was 0.94145223 days. The formal depth S/N is not a probability of a planet.

WASP-18 also has a detectable secondary eclipse. The residual search records same-period dips as residual features of the existing signal and masks them before looking for another period. A secondary-eclipse flag is a reason to inspect competing explanations, not proof that every such object is a binary star.

For another target, enter a name, `TIC 4206066`, or coordinates understood by MAST/Lightkurve. **Find observations** lists available sectors. Select sectors explicitly for comparisons across observing years. With no selection, observations are processed oldest first, up to the configured maximum. Only one cadence per sector and one resolved target are combined; 120-second TESS products are preferred.

The supplied research-reference target is **not** advertised as a reproduced discovery. A blind Sector 32-only search did not uniquely recover the paper's 3.18-day result. Its weak signals require additional observations, processing controls, and localization. You can test a competing period in Transit analysis, or ask the assistant to run `compare_period` with 3.182785 days. A targeted recovery after reading a paper is not independent discovery evidence.

## Implemented workflow

- Real TESS SPOC, Kepler and K2 calibrated PDCSAP light-curve searches through Lightkurve/MAST.
- Named-target, TIC-ID, coordinate, and newline-delimited batch input (up to 1,000 targets).
- Local FITS cache, SHA256 checks, source URLs, download dates, pipeline releases, quality masks, and processing provenance.
- Astropy BLS over configurable duration/period ranges; a refined peak search; iterative masking of detected signals.
- Per-sector/per-gap normalization and running median or robust Savitzky–Golay detrending, followed by a transit-protected second pass. Normalize-only is available as a control.
- Formal depth/error/S/N, sampled event coverage, odd/even depth comparisons, phase-0.5 secondary searches, sector measurements, preprocessing sensitivity, gap proximity and search-boundary checks.
- Cadence-integrated `batman` fits with explicit fixed assumptions; approximate conditional fit errors; approximate planet radii when FITS stellar radii exist.
- NASA Exoplanet Archive confirmed-planet and TOI lookups with timestamps and explicit unavailable states. Positional and period agreement are both required for associations.
- Gaia DR3 neighbor queries, exploratory TESS pixel difference images and aperture comparisons, alternate-period tests, and randomized-phase box injection recovery.
- Interactive Plotly light curves, periodograms, folded data/models, event tables, evidence, provenance, history, and persistent research chat.
- Durable SQLite queue with separate scientific subprocesses, cancellation, time limits, bounded numerical threads, and restart/resume controls.
- Self-contained HTML reports; JSON, full-resolution CSV/NPZ, raw FITS, executable Python/notebook and source snapshots in reproduction ZIPs.
- Optional tool-calling assistant with OpenAI Responses, Anthropic Messages, Gemini/OpenRouter OpenAI compatibility, and local OpenAI-compatible endpoints.

There is no randomized or fabricated observational data in the application. Synthetic data are restricted to automated tests and explicitly labeled injection experiments.

## AI configuration

Open **AI settings** and choose a provider. Enter your key, an exact model identifier, and **Save settings**. **Load models** queries that provider; manual model entry also works. **Test connection** makes one small real request.

- OpenAI uses `/v1/responses`, including native function-call outputs and reasoning items.
- Anthropic uses `/v1/messages` with native `tool_use` / `tool_result` blocks. Leave the reasoning selector at provider default; Anthropic reasoning controls are model-specific.
- Gemini uses its documented OpenAI-compatible endpoint.
- OpenRouter uses its OpenAI-compatible endpoint.
- LM Studio commonly uses `http://127.0.0.1:1234/v1`; Ollama commonly uses `http://127.0.0.1:11434/v1`. Select a model that supports tools. Local endpoints are restricted to loopback addresses.

Temperature and reasoning are optional. Leave them at provider defaults for models that do not support those controls. Model availability, billing, and supported options depend on your provider account. The app reports provider errors rather than silently substituting a model.

Keys are stored with `keyring` in the OS credential store (Windows Credential Manager on the tested platform). There is **no plaintext fallback**. If an OS keyring is unavailable, credential saving fails instead of writing an unprotected key. Public settings only indicate whether a credential exists. Credentials are redacted from errors, stored transcripts and tool results; provider error bodies are not persisted. Do not enter secrets into chat. Cloud sessions send conversation context and requested numerical results to the selected provider; the core scientific pipeline stays local.

Set the selected model's actual input/output token prices before starting a cloud research session. The app reserves a conservative cost before each request using those user-supplied rates and the output-token cap. This protects the configured session budget under those prices; it is **not** a guarantee about provider-side charges, unknown pricing changes, or billing already incurred during interrupted calls. Local endpoints can use zero prices.

The assistant has validated tools for archive search, TIC lookup, deterministic analysis, report inspection, alternate-period search, Gaia, target pixels, injection recovery, and listing investigations. There is no arbitrary code or shell tool. Model calls, scientific operations and tools per iteration are bounded. Each analysis is limited to three light-curve products and 300 MB; pixel retrieval is separately capped at 300 MB. Sessions stop at their job time limit. Completed evidence and the provider transcript are checkpointed; incomplete scientific work may rerun from cached inputs on resume. Potentially interrupted API calls still consume the reserved allowance.

No paid-provider credential was supplied during development. All five wire protocols and tool-result round trips were tested using controlled test responses; a live model/account connection remains to be verified with your key. The application does not claim an LLM answered a research question unless a real configured provider returned that answer.

## Automated research from a prompt

1. Open **AI settings**. Select your provider and model, save your API key, enter the model's token prices, then use **Test connection**. Local tool-capable models do not require paid API credentials.
2. Open **Automated research** in the sidebar. Write your goal, or choose **Recover a known planet** for a first run.
3. Expand **Run limits** if needed. Defaults are 3 targets, 10 scientific operations, 20 model calls, 600 MB of distinct input FITS files, 60 minutes per attempt, and a $1 conservative API allowance. Price reservations may stop a run before the full nominal budget is used.
4. Click **Start research**. The AI saves a plan, chooses parameters, calls deterministic scientific tools, reads the resulting investigations, and saves its assessment. The run screen shows the plan, real regional query results, actual parameters, tool inputs/outputs, resource use and findings.
5. Use **Open investigation** to inspect light curves, periodograms, catalog matches and validation evidence. **Report** and **JSON** export the research run. Each scientific investigation retains its own complete reproduction export.

Example goals:

> Analyze the most likely region of the sky for exoplanets. Compare TESS coverage around both ecliptic poles, select promising small, bright stars, and investigate up to 3 targets. Explain selection bias and check signals against known planets and TOIs.

> Analyze WASP-18 in sector 2 as a known-positive control. Choose the search parameters, recover its transit and assess the evidence.

> Investigate TIC 4206066. Check whether the strongest periodic signal survives alternative preprocessing and whether an eclipsing binary could explain it. Identify tests that remain incomplete.

A regional request triggers real filtered **TIC catalog** queries and **MAST SPOC observation** queries. The AI can set a custom ICRS cone, brightness/radius limits, minimum observing sectors and a transparent sorting rule. The implemented default compares bounded cones around the two ecliptic poles. Sector count is deduplicated across cadence products. Brightness, stellar size and observing coverage are **sensitivity proxies, not planet probabilities**. The tool does not search the entire sky, guarantee an optimal region, or establish that targets are uncataloged. Subsequent analysis performs the known-planet/TOI checks.

**Stop** cancels the worker subprocess. **Resume** continues cancelled, interrupted or failed runs from saved state; an interrupted individual calculation can rerun from cached inputs. Stable operation IDs prevent duplicate saved investigations/follow-up records on replay. Model, limits and non-secret configuration are pinned for the run. API calls interrupted after reservation still consume allowance. The time limit applies to each execution attempt; other run allowances persist across resumes. Budget/iteration-limit outcomes remain explicitly incomplete and retain evidence; start a new goal to continue with a different allowance.

A cloud model receives the prompt and requested tool results. Keys remain in the local OS credential store. There is no arbitrary code-execution tool. A model cannot mark the run complete merely by writing that it finished: it must save a plan, execute a real evidence tool, read every newly created investigation, and submit a structured assessment. Scientific classifications remain computed by the pipeline; the final AI assessment is labeled as interpretation and should be checked against the linked evidence. Provider availability and tool-calling reliability can still interrupt a run.

## Scientific boundaries

This is the functioning first vertical slice, not a planet-validation pipeline equivalent to the full cited study.

- **Classification is rule-based triage, not confidence probability.** Failed catalog lookups remain unavailable, not evidence of novelty. A known association does not certify that a nearby source cannot be responsible. TOI false-positive dispositions remain false positives.
- BLS depth errors and least-squares covariance assume a conditional noise/model description. **Calibrated red-noise errors, period/epoch posterior uncertainties, survey false-alarm rates, and astrophysical FPP are not implemented.** Grid spacing is not an uncertainty.
- Fixed circular orbit, period, and quadratic limb darkening `[0.3, 0.2]` make the physical fit exploratory. No density prior, limb-darkening inference, dilution model, posterior sampler, eccentric fit, or robust grazing-geometry inference is included.
- Radius uses `sqrt(box depth) × catalog stellar radius × 109.076`. Its unknown stellar/dilution uncertainty is disclosed. No reliable orbital mass or absolute distance is invented.
- Broad, year-separated BLS searches are capped at **150,000 periods before allocation**. Capped searches are labeled incomplete because narrow peaks can be missed. Narrow the period range, test a proposed period, and compare separate sectors. No claim of exhaustive multi-year sensitivity is made.
- Quality-filtered data are not clipped on the low-flux side. The trend window must be at least five times the maximum searched duration. Gap/edge and boundary diagnostics can invalidate a promising interpretation. Preprocessing controls are fixed-ephemeris depth comparisons, not independent searches.
- Sector agreement is measured at the combined ephemeris, not held-out-year prediction. Independent individual transit-time fitting and pre-registration management are future work.
- Pixel tests compute an exploratory out-minus-in image and aperture-depth comparison. They do **not** perform a calibrated PRF fit, pointing decorrelation or centroid uncertainty calculation and cannot establish the host star. Gaia positions are not propagated from their catalog epoch; G magnitudes are not TESS magnitudes. Unresolved companions remain possible.
- Injection recovery uses box signals at one period/depth, random phases, and masks previously found events. It estimates conditional residual sensitivity, not a population completeness curve or a false-positive probability.
- TESS input currently supports **SPOC calibrated light curves**, not QLP/TESS-SPOC FFI light curves, TESScut photometric extraction, or a survey of every star in the full-frame images. Kepler/K2 adapters use public calibrated light curves but were not live-verified during this build.
- NASA confirmed planets and TOIs are queried automatically. ExoFOP is linked for manual review; community TOIs, TCE catalogs, follow-up observations and the literature are not exhaustively ingested. “Unclassified” does not mean “previously unreported.”

The next substantial additions are broader survey selection beyond bounded TIC/SPOC cones; multi-sector frequency refinement with injection-calibrated completeness; independent transit timings; PRF-based localization; expanded catalog/follow-up ingestion; physical posterior inference; longer survey orchestration; and an inferred-system 3D view. These are deliberately not represented as functioning controls in this version.

## Reproducibility and storage

All runtime state lives under `data/`, excluded from Git. Set `EXO_DATA_DIR` to use another directory.

```text
data/research.sqlite             jobs, cache metadata, investigations, chat, non-secret settings
data/observations/               original FITS and provenance sidecars
data/artifacts/<investigation>/  full arrays, periodograms, JSON and analysis-time source snapshot
data/artifacts/<chat-job>/       resumable provider transcript and budget accounting
data/artifacts/<research-job>/   automated plan, transcript, operation IDs and input-byte ledger
```

The ZIP contains full-resolution data; the interface samples large light curves for display and preserves narrow periodogram peaks. Folded plots mask earlier signals for subsequent candidates. Reports include measurements, source product identifiers, lookup dates, assumptions, diagnostic failures, unknowns, and machine-readable metadata.

Extract an investigation ZIP into a new folder, create a Python environment, then:

```powershell
pip install -r requirements-science.txt
pip install ./source
python reproduce.py
```

The script regenerates both preprocessing passes and each saved BLS search from the original quality-filtered flux arrays. Original FITS are included for independent inspection of quality selection. The notebook contains the same executable recipe. Source hashes identify the analysis-time implementation. Version pins cover scientific dependencies; platform-specific transitive binary packages can differ and the recorded Python version should be respected.

Do not expose this server publicly. It binds to loopback, rejects foreign browser origins and unexpected hosts, and has no multi-user authentication. It is designed for a single trusted OS user.

## Development and tests

```powershell
.venv/Scripts/python -m pytest -q
$env:EXO_NETWORK_TESTS='1'
.venv/Scripts/python -m pytest backend/tests/test_live_archive.py -q
cd frontend
npm run build
npx playwright test
```

The Playwright suite expects the app running on port 8765 with at least one real completed WASP-18 investigation. The live integration test accesses MAST/NASA and fails if the known planet is not actually recovered and catalog-associated. Unit tests use isolated temporary stores and explicitly synthetic signals, including deliberately failing astrophysical diagnostics.

For hot frontend development, run `npm run dev` in `frontend/` with the backend/worker launcher running. The Vite proxy forwards `/api` to port 8765. FastAPI's API documentation is at `/docs`.

See [docs/methodology.md](docs/methodology.md) for how the source research informed this implementation.
