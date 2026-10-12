# Improvements following the overnight search

Implemented October 12, 2026. These improve evidence accounting and operation; they do not statistically validate planet candidates.

## Diagnostics and follow-up

- Acquisition edges are computed before previous-signal masks. Removing a searched signal no longer invents acquisition gaps.
- Counts are labelled covered predicted windows, not observed dips. The legacy `observed_transits` field remains for compatibility.
- Sector depth differences and leave-one-event-out depth S/N are reported, with explicitly heuristic review flags. Formal errors do not include correlated noise or shared-baseline covariance.
- Preprocessing comparisons also check disagreement with the original searched depth. Nonconverged shape fits receive a visible diagnostic flag.
- Follow-ups refresh the evidence summary and triage classification. An inconclusive localization remains inconclusive; poor injection recovery alone is not evidence of an astrophysical false positive.
- Alias comparisons respect masks from earlier signals in the same investigation.
- Fixed-ephemeris comparisons use disjoint sectors from another saved investigation, preserve period/epoch/duration, and detrend raw comparison flux independently. No timing uncertainty propagation or blinded-data claim is made.
- Injection duration is explicit and preserved. Each trial records injected cadences and covered windows. Unobservable trials are distinct from searched failures, and both recovery denominators are exposed.
- Completed follow-up checkpoints are reused on resume, including jobs without an orchestrator operation ID.

## Catalogs and products

- Product preflight reports requested/selected/missing sectors and product-limit exclusions. Partial explicit sector selections require an opt-in.
- Catalog-only refresh updates all saved signal comparisons and retains prior catalogs in history. Failed queries remain unavailable.
- Optional known-host exclusion during discovery skips unavailable catalog comparisons conservatively and records omissions.
- Gaia queries order by angular separation before limiting results, request an extra row to detect truncation, and use spherical separation. Target identification and proper-motion propagation remain outstanding.
- Pixel diagnostics accept an investigation sector and cadence, and inherit the strict/default quality filter.

## MCP, queue and reports

- Five additional tools: product preflight, catalog refresh, fixed ephemeris, event pagination, and suggested signal groups.
- Compact evidence reads explicitly enumerate omitted arrays and truncated lists. Full reads and exports remain available.
- Durable submission keys prevent duplicate jobs after lost responses. Same-process retries reuse their submission reservation.
- Queue priority, deferral and pause use atomic state transitions. Resuming joins the queue at the current time.
- Stalled remote-search stages stop after 180 seconds without a stage change. Download progress is recorded; overall numerical limits remain in force.
- Suggested same-target/near-period groups help identify repeated investigations. They do not merge measurements, establish candidate identity or combine aliases.
- HTML reports retain full-scale plots and add labelled flux-detail plots, pixel heatmaps, aperture outlines and follow-up summaries.
- The workbench exposes catalog refresh, pixel-sector selection, frozen-ephemeris comparison and queue controls. Injection results distinguish unobservable trials.

## Deferred work

Proper-motion-aware host assignment, reliable distance selection, calibrated PRF/centroid localization, correlated-noise and false-alarm calibration, resolution-safe long-baseline period searches, ensemble systematics rejection, improved stellar-variability models, transit posterior inference, and astrophysical false-positive probabilities remain separate scientific projects. Suggested signal groups are not a persistent reviewed candidate registry. Running-job suspension and parallel workers are not implemented.

Existing investigations retain their original numerical results and analysis-time source snapshots. New diagnostics require a new analysis; catalog refresh and new follow-ups update only their documented evidence. Restart the workbench and reconnect MCP clients after updating the code.

## Verification

- Python suite: 58 passed, one opt-in live-network test skipped. The affected MCP/store/regression subset was also rerun after the final queue and protocol changes.
- Frontend production build passed. Six browser scenarios passed, including the corrected follow-up controls; new follow-up submissions in that UI test were intercepted rather than sent to real jobs.
- Real stdio MCP initialization exposed all 22 tools and read the running workbench successfully.
- Replaying cached WASP-18 FITS through the updated analysis recovered 0.9414608208 days and the saved known-planet catalog association, with 27 covered predicted windows. Catalog responses were replayed, not freshly queried.
- Two injection trials on cached TIC 150139182 data preserved the saved 2.3667-hour duration. One injected zero cadences and was correctly marked unobservable; the other injected 5,682 cadences with 79 covered windows and recovered. This two-trial regression exercise is not a calibrated completeness estimate.
- A retrospective fixed-ephemeris comparison ran on the disjoint saved sector sets without refitting. Timing uncertainty remains uncalibrated.

Replay evidence is saved locally under `reports/improvement-checks-2026-10-12/`. Replays used isolated temporary stores and did not rewrite the overnight investigations. Known existing build warnings concern the large Plotly bundle and optional/deprecated dependencies; these do not indicate a failed test.
