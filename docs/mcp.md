# Connect Claude or a local model through MCP

The MCP server lets an external assistant choose targets and analysis parameters, run scientific jobs, inspect results, launch validation tests, and retrieve reports. The client supplies the LLM. **You do not need to configure an AI provider or key inside EXODISCOVERY for MCP.** Numerical work uses the same real archive clients, deterministic pipeline, local storage and durable queue as the web application.

## Start the workbench

From the project folder:

```powershell
python run.py
```

Keep it running. The browser is at http://127.0.0.1:8765. The launcher installs the MCP dependency on first startup after this update. If using a previously installed environment manually, update it with:

```powershell
.venv\Scripts\python -m pip install -e ".[test]"
```

The MCP adapter does not start a second worker, launch another LLM, or own the database. It connects to this running local application. Disconnecting Claude leaves queued scientific jobs running; their progress remains visible under **Research queue**.

## Claude Desktop

Generate a ready-to-paste configuration with absolute paths for this checkout:

```powershell
.venv\Scripts\python mcp_server.py --print-config
```

A generated copy for the current checkout is also in `mcp-config.local.json` (excluded from Git because paths are machine-specific).

In Claude Desktop, open **Settings → Developer → Edit Config**. On Windows the file is `%APPDATA%\Claude\claude_desktop_config.json`. Merge the generated `exodiscovery` entry into its `mcpServers` object, preserving your other servers, then restart Claude Desktop. This follows the [official local-server configuration guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

The configuration has this shape; use the generated values instead of the placeholders:

```json
{
  "mcpServers": {
    "exodiscovery": {
      "command": "C:\\path\\to\\ExoplanetWorkbench\\.venv\\Scripts\\python.exe",
      "args": [
        "C:\\path\\to\\ExoplanetWorkbench\\mcp_server.py",
        "--max-jobs", "20"
      ]
    }
  }
}
```

The host launches this stdio server itself; do not run the same stdio command in a separate terminal expecting Claude to attach to it. Client tool approval settings determine whether tools execute automatically or require approval.

## LM Studio and other local-model clients

Use a tool-capable model in an MCP host. In LM Studio, open the **Program** sidebar and **Install → Edit mcp.json**, then merge the same generated `exodiscovery` entry. These controls and the configuration format are documented in [LM Studio's MCP guide](https://lmstudio.ai/docs/app/mcp).

An Ollama inference endpoint by itself does not perform this research loop: connect the model through an MCP-capable agent/client that can call tools repeatedly and read their results. EXODISCOVERY's own built-in **Automated research** mode remains a separate option if you prefer to use its UI and provider settings.

## Streamable HTTP clients

For a client supporting Streamable HTTP, run a separate terminal:

```powershell
.venv\Scripts\python mcp_server.py --transport streamable-http
```

Connect the client to **http://127.0.0.1:8766/mcp**. A typical URL-based client configuration is:

```json
{
  "mcpServers": {
    "exodiscovery": { "url": "http://127.0.0.1:8766/mcp" }
  }
}
```

The server binds only to loopback and checks HTTP Host/Origin headers. It has no remote authentication layer and is intended for trusted clients on this computer. There is no public deployment or LAN listener. Stdio is the simplest option for Claude Desktop. The adapter uses the maintained, pinned v1 line of the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk/tree/v1.x).

## Give the assistant a goal

Try a known-positive control first:

> Use EXODISCOVERY to analyze WASP-18 in TESS sector 2. Check the workbench connection, choose search parameters, run the analysis, wait for completion, read the actual investigation, and explain which known planet was recovered. Link the report and identify the limits of the evidence.

Then try a bounded selection and investigation:

> Use EXODISCOVERY to compare half-degree regions around the north and south ecliptic poles. Select up to two small, bright stars with multiple TESS sectors, analyze their observations, and investigate plausible false positives. Use at most 15 job submissions. Explain why you selected those targets and report any unfinished tests. Do not interpret observing coverage as a probability of planets.

The server supplies an `investigate` prompt template and `exodiscovery://guide` resource with this workflow. Whether your host exposes prompts/resources in its interface varies; the tools work independently of those UI features.

## Tools and results

| Tools | Behavior |
| --- | --- |
| `workbench_status` | App health, actual dashboard counts, workflow and remaining MCP quota |
| `search_observations`, `query_stellar_catalog` | Queue MAST product or TIC catalog searches |
| `discover_targets` | Queue filtered TIC/SPOC coverage comparisons in polar or custom cones |
| `analyze_target` | Queue calibrated data retrieval, preprocessing, BLS, transit fits, diagnostics and known-planet/TOI comparisons |
| `compare_period`, `nearby_sources`, `inspect_pixels`, `injection_recovery` | Queue focused numerical/catalog follow-up tests |
| `get_job`, `wait_for_job`, `list_jobs`, `control_job` | Inspect progress/results, wait briefly, cancel or resume scientific jobs |
| `list_investigations`, `read_investigation` | Read persistent computed evidence, parameters, provenance and validation results |
| `get_plot_data` | Paginated real numerical light-curve, periodogram or folded-curve previews, up to 2,000 points per page |
| `get_report` | Computed findings plus HTML report, machine-readable JSON and reproduction ZIP URLs |

Submission tools accept a typed `config` object. For example:

```json
{
  "config": {
    "target": "WASP-18",
    "mission": "TESS",
    "sectors": [2],
    "max_products": 1,
    "min_period": 0.5,
    "max_period": 10,
    "max_signals": 1
  }
}
```

They return a **job ID**, not a completed finding. Poll `get_job` or call `wait_for_job` with that ID. For completed analysis jobs, `result.id` is the investigation ID for subsequent read/follow-up calls. Scientific inputs and results remain in the shared workbench; the external assistant's conversation, plan and written interpretation remain in its own client. The MCP adapter does not save an external conversation as a built-in Automated research run.

## Limits and scientific interpretation

- Default quota: **20 submissions/resumes per MCP server process**, including archive queries and follow-ups. Read/status/cancel tools remain available after the quota. Set `--max-jobs 50` in the generated entry to change it. Quotas reset when the MCP server restarts; this is not a durable total-survey limit.
- Each MCP analysis permits at most **3 products, 300 MB of selected input files, and 1,800 seconds**. Oversized requests are rejected. Existing schema limits apply to injection trials and other parameters. Other queued jobs use the existing worker time limits. The one scientific worker limits concurrent computations.
- There is **no MCP-wide cumulative download/disk cap or billing cap for the external client**. Configure model spending/iteration limits in your host. MCP never calls the application's configured AI provider.
- Jobs survive disconnects. Use `control_job` to stop them; resume cancelled, failed or interrupted work. Completed artifacts and cached observations are reused where the pipeline supports checkpoints.
- MCP exposes scientific jobs and investigation data, not provider configuration, secrets, conversations, shell execution, arbitrary URLs or arbitrary local files.
- Reports contain deterministic findings and disclosed assumptions. External model prose is interpretation, not additional evidence. A transit signal is not a confirmed planet. Catalog query failure does not establish novelty. Region rankings describe available observing sensitivity rather than a globally optimal sky location.
- Plot previews are sampled and paginated. Full arrays, original FITS and executable reproduction material are in the investigation ZIP. Do not infer scientific completeness from a preview page.

## Verify the connection

With the workbench running, test the real stdio protocol:

```powershell
.venv\Scripts\python scripts/check_mcp.py
```

To exercise an actual queued TESS analysis and require the known WASP-18 recovery:

```powershell
.venv\Scripts\python scripts/check_mcp.py --recover
```

For the separately running HTTP MCP server:

```powershell
.venv\Scripts\python scripts/check_mcp.py --http
```

These checks use the SDK's real MCP client, not a paid LLM. `--recover` downloads or reuses original public FITS, executes the numerical pipeline, verifies the catalog association, and reads the resulting evidence/plot previews through MCP. Client-specific model planning quality still depends on the configured model.

If tools report the workbench unavailable, start `python run.py`. If a newly added tool receives an unexpected response, restart the workbench after updating its code. If a submission times out, check `list_jobs` before retrying because it may already have been queued. Invalid scientific parameters, missing evidence, and quota exhaustion return MCP tool errors rather than fabricated results.
