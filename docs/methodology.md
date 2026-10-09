# Research basis and implementation decisions

The requested [Reddit post](https://www.reddit.com/r/ClaudeAI/comments/1wzw8zd/i_think_i_found_a_planet_nobody_knew_existed_i/) links Pavel Rabtsevich's [TIC 4206066 manuscript](https://doi.org/10.5281/zenodo.22967456), [supporting data/code](https://doi.org/10.5281/zenodo.22967099), and [Sector 110 preregistration](https://doi.org/10.5281/zenodo.23175179). The manuscript and full preregistration were retrieved through Zenodo's public API and read during implementation. Metadata from the two principal records are preserved in `research-sources.json`.

The study combines heterogeneous photometry from TESS-SPOC Sector 6, SPOC Sector 32, and QLP Sector 98. It searches with BLS, examines individual event coverage, models finite exposures, compares sector and alternating-event depths, tests secondary eclipses, investigates source localization and companions, and treats the weaker second signal separately. Its astrophysical validation was not established by a recurring dip alone.

The preregistration is especially informative about failure modes: post-selection significance, mismatched preprocessing for injected/null events, insufficient baseline around events, data-gap edges, dependence between overlapping trials, and uncertainties in localization. It distinguishes a future observation made under frozen rules from a reanalysis of archival data already inspected. This workbench does not implement that frozen protocol or claim to reproduce its validation outcome.

Implementation choices:

1. The deterministic backend owns every numerical result. The model can select validated tools but cannot supply fabricated measurements or execute code.
2. Actual input provenance, quality masks, processing choices, code hashes, software versions, catalog dates and unperformed tests are first-class saved data.
3. A detection is a transit-like signal. Strong formal S/N does not eliminate binary, companion, variable-star, contamination or instrumental scenarios.
4. Failed or empty evidence is distinguished from a completed test with a negative result. A failed catalog connection cannot establish an uncataloged signal.
5. Sector boundaries and >0.3-day gaps are not detrended across. Lower flux values are retained. A second detrending pass protects the initially detected transit.
6. At least three adequately sampled events are needed for a signal to leave the insufficient-evidence state. Baseline coverage, search boundaries and processing sensitivity are recorded. These are conservative heuristics, not statistically calibrated significance thresholds.
7. Same-period residuals, including occultations, are retained as diagnostics of an existing orbit and are not counted as additional planet candidates.
8. The exploratory physical model uses batman rather than hand-coded transit geometry. Its assumptions and conditional errors remain visible. The system does not compute a false-positive probability.
9. Injection experiments occur before detrending, with fixed seeds and measured recovery outcomes. They are described as conditional sensitivity tests; random phase injection alone does not establish survey completeness.
10. Analysis results and their exports remain useful with no model provider configured. Provider transcripts, operation counts and budget reservations survive interruptions.

Primary implementation references:

- [Lightkurve: searching and downloading calibrated products](https://lightkurve.github.io/lightkurve/tutorials/1-getting-started/searching-for-data-products.html)
- [Astropy BoxLeastSquares](https://docs.astropy.org/en/stable/api/astropy.timeseries.BoxLeastSquares.html)
- [NASA Exoplanet Archive TAP](https://exoplanetarchive.ipac.caltech.edu/docs/TAP/usingTAP.html)
- [batman transit modeling](https://lkreidberg.github.io/batman/docs/html/index.html)
- [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Anthropic tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview)
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai)
- [MAST filtered TIC catalog queries](https://astroquery.readthedocs.io/en/stable/mast/mast_catalog.html)
- [MAST observation metadata queries](https://astroquery.readthedocs.io/en/stable/mast/mast_obsquery.html)
- [TESS observing strategy and polar coverage](https://tess.mit.edu/mission-overview/)

These sources inform the implementation; they do not independently certify the numerical pipeline. Scientific validation still requires adversarial tests, properly calibrated uncertainties, scrutiny of original observations, and external review.
