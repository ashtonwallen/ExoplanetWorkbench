# Verification performed during this build

All observational examples below used public archive data, not embedded fixtures.

## Scientific checks

- WASP-18, SPOC TESS Sector 2: downloaded the original calibrated FITS, retained 18,299 cadences after quality/finite-flux filtering, and recovered a period of **0.9414608208045275 days**. The NASA catalog association was **WASP-18 b**.
- WASP-18, Sectors 2 and 3: recovered **0.9414543255001093 days**, with **46** events satisfying the implemented sampling/baseline coverage rule. A weaker residual at another period was classified as insufficient evidence.
- TIC 4206066, Sector 32: the two strongest blind-search peaks were classified as insufficient evidence after event-coverage, preprocessing and boundary diagnostics. No independent recovery of the paper's discovery was claimed.
- The Gaia cone query for WASP-18 returned **21** sources. This is a real neighbor list, not an exclusion of unresolved companions.
- The Sector 2 target-pixel analysis measured approximately **9,306 ppm** in the SPOC aperture and **9,370 ppm** in an eroded aperture. This is exploratory aperture consistency, not calibrated source localization.
- Three **2,000 ppm, 3.18-day** box injections into the masked WASP-18 residual observations were recovered. Three trials establish neither a precise completeness fraction nor a false-positive rate.
- The two-sector reproduction ZIP was extracted, and its `reproduce.py` executed using the included source through `PYTHONPATH`. Both saved BLS periods were reproduced exactly.

## Automated checks

- **34 offline tests passed**: transit injection/search, multiple detrending controls, sector discontinuities, deliberately failing eclipsing-binary checks, missing-data behavior, host/period associations, no duplicate cadence selection, batman fit, configuration validation, quality-bitmask selection, time-system rejection, atomic queue claims, restart/resume state, cancellation, saved checkpoint reuse, browser-origin/host protection, credential redaction, local endpoint constraints, unsupported-tool rejection, provider tool protocol round trips, and pre-request budget enforcement.
- **One separate live integration test passed**: downloads real WASP-18 data, independently measures its period, and requires an actual NASA known-planet association.
- **Five Playwright browser tests passed**: real plots and evidence, provider settings and archive search, persistent investigation reopening, queue access and narrow viewport overflow.
- TypeScript checking and the optimized Vite production build passed.
- A running 40-trial injection job was cancelled through the API. The worker terminated the science subprocess and reported `cancelled`. A queued archive-search job was cancelled and resumed, then completed.
- HTML report and ZIP export endpoints returned usable artifacts. A scientific reproduction was executed, rather than treating a successful download as sufficient verification.

## Automated research verification

- The live TIC/MAST comparison of two 0.5-degree ecliptic-pole cones completed. With Tmag <= 12, stellar radius <= 1.5 solar, 2500?6500 K and at least two SPOC sectors, the north cone contained 10 filtered TIC stars and 5 eligible observed stars; the south cone contained 12 and 10 respectively. These are bounded query results, not all-sky planet-yield estimates. Query timestamps and returned catalog records are cached locally.
- TIC 219093943 was selected from those real query results. Its Sector 56 FITS file was downloaded under the new cumulative input allowance: **2,039,040 bytes**, **19,612** quality-filtered cadences. A 0.5?8-day search returned a strongest period of **6.6657405338 days**, classified **Likely false positive**. No planet discovery was claimed. The retained investigation is `a32872af19884cec`.
- Nine additional offline tests cover deduplicated sector ranking/filtering, archive failure versus absence, configuration/credential boundaries, mandatory plan and evidence review, text-only completion rejection, spending checks before provider calls, cumulative persistent FITS-byte limits, operation/target quotas and stable replay IDs, and a complete scripted-provider loop that executes the actual deterministic science pipeline on explicitly synthetic test observations. The last test interrupts the provider after science is saved, resumes, reads the saved evidence and completes without creating a duplicate investigation.
- Two additional browser tests cover the real automated-mode configuration/limits interface and a browser-only scripted run for submission, chosen parameters, findings, report links and stop/resume. Scripted fixtures never enter the application database. Production build and screenshots were checked.

## Limits of verification

Cloud provider protocols were tested with controlled transport responses. No user's paid API key was supplied, and no claim of a live paid-model conversation is made. Key storage reports Windows `WinVaultKeyring`; production key values are never sent back to the frontend. Kepler and K2 downloads and other operating systems were not independently exercised.

The optional Lightkurve `oktopus` warning concerns its unused PRF modeling module. The implemented pixel tool intentionally does not claim PRF localization. The frontend build contains a large locally bundled Plotly package; no plotting CDN is required at runtime.
