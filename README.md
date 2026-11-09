<div align="center">

# SupplyChainAgent

**Organizational enterprise agents in a configurable supply-chain economy**

Organizational Agent Skills &nbsp; | &nbsp; Executable Economy &nbsp; | &nbsp; Matched Experiments &nbsp; | &nbsp; Evidence Review

[Quick Start](#quick-start) · [Dataset](#published-experiment-data) · [Run Experiments](#agent-setup) · [Research Workflow](#research-workflow) · [Platform Tour](#experiment-interface) · [Scenarios](#scenarios) · [Documentation](#documentation)

</div>

SupplyChainAgent is a research platform for studying how LLM agents operate as enterprises and how their decisions produce supply-chain dynamics. Enterprise analysts and department Skills work with role-scoped observations and a private blackboard. A programmatic economic environment validates their proposed actions and advances inventory, cash, capacity, production, deliveries, and transaction commitments across simulation rounds.

The repository brings together the **enterprise agent architecture**, **economic execution engine**, **controlled experiment suite**, and **English results-review interface** developed for this research. Use Agent Skills for model-driven decisions or a scripted controller for deterministic reference runs. Visitors can explore published results without a model account; operators can configure independent experiments on their own installation. The [published paper dataset](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/tag/data-v1.0.0) provides **46 experiment archives** for local review.

![Architecture: enterprise agents exchange structured actions and observations with a supply-chain environment.](docs/assets/architecture-overview.png)

*Architecture overview (Figure 1 in the manuscript). Enterprise analysts, role-specialized agents, and private blackboards interact with a programmatic supply-chain environment through structured actions, observations, and feedback. The topology shown is an example; firms, departments, information visibility, initial state, and economic mechanisms are configured per scenario. [Open the full-resolution PDF](docs/assets/architecture-overview.pdf).*

## What the Platform Provides

| Layer | Implemented capabilities | Research use |
| --- | --- | --- |
| Enterprise organization | Analyst, functional department Skills, authorized observations, private coordination, and a single-agent alternative | Compare diagnosis, coordination, and division of responsibility |
| Economic execution | Action validation, execution receipts, inventory, staffing, capacity, production, and finance | Connect proposed decisions to actual business-state changes |
| Inter-firm exchange | Requests, proposals, bilateral acceptances, confirmed orders, delivery, and settlement | Follow transaction lifecycles and operating dependencies |
| Experiment design | Five enterprise diagnosis cases, four economic mechanisms, topology presets, and long-horizon shocks | Study firm capabilities and system-level dynamics under configured conditions |
| Experiment management | Frozen job configurations, isolated workspaces, feedback-driven retries, serial batches, checkpoints, and source/Skill fingerprints | Execute and inspect comparison groups with separate run artifacts |
| Evidence review | Round-level states, decisions, messages, receipts, metric definitions, charts, comparisons, and downloads | Trace an observed outcome back to the records that produced it |

## Research Workflow

1. **Choose a question and a scenario.** Configure enterprise constraints, network structure, information visibility, controller conditions, or scheduled shocks through the scenario catalog.
2. **Run controlled comparisons.** Compare Agent and scripted controllers, or select Full/MA, NA, NC, and SA conditions for FD1–FD5. An automatic batch runs selected conditions in sequence, creating a fresh job, worker, configuration, and output directory for each item.
3. **Inspect execution as well as outcomes.** Follow diagnosis and department actions through execution feedback, then examine fulfillment, production, recruitment, liquidity, and inter-firm transactions. Task completion, valid model output, successful business execution, and economic performance are reported as distinct signals.
4. **Trace and export the evidence.** Review individual rounds, follow propagation across firms, compare archived runs, and download available records. Units, denominators, snapshot coverage, and source paths accompany the metrics.

The [architecture guide](docs/architecture.md) explains the round lifecycle. The [matched-experiment guide](docs/experiments.md) describes ablation conditions, frozen request manifests, batch behavior, and evaluation exports.

## Quick Start

Choose how you want to use the platform:

| Your goal | Deployment mode | Model credentials | Next steps |
| --- | --- | --- | --- |
| Browse existing results and inspect experiment designs | **Visitor** | None | Install below, import the example, and start the visitor viewer |
| Run a scripted reference experiment | **Operator** | None | Install below, then follow [operator setup](#agent-setup) and select a C1 / Fixed Rule Script condition |
| Run experiments with LLM agents | **Operator** | Your own model service | Install below, then follow [model and operator setup](#agent-setup) |

A public visitor website provides result viewing. New experiments require an operator installation with its own execution access and, for Agent conditions, a working model service.

### 1. Download and Install

The commands below use Bash and Unix-style virtual-environment paths. Use **Python 3.12** for the documented local setup; it was verified with Python 3.12.3, Node.js 18.19.1, and npm 9.2.0. Install Python, Node.js/npm, and Git before proceeding.

```bash
git clone --branch open-source --single-branch \
  https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim.git
cd SupplyChainAgent-OrgEconSim

python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-tested.txt
npm --prefix visualization ci
```

If you downloaded a ZIP or already cloned the repository, start with `python3 -m venv .venv` from the extracted/cloned repository root: the folder containing `README.md`, `agent/`, `scripts/`, and `visualization/`. Check `python3 --version` first; use your Python 3.12 executable to create the environment if `python3` selects another version. On systems where `venv` is packaged separately, install the matching Python venv package first.

Keep npm development dependencies installed: they include the Vite build tools. These commands install the Python service and frontend dependencies; they do not start simulations or use a model account.

### 2. Load Data and Start Visitor Mode

Choose one data source:

- **Included compact example:** use the command below for an initial FD4 walkthrough. It needs no external data download or 7-Zip installation.
- **Published paper experiments:** download any of the 46 `.7z` archives from [Simulation Dataset v1.0.0](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/tag/data-v1.0.0), then follow [download and extraction](docs/data_download.md#download-and-extract-data). The dataset includes FD1–FD5, all four economic mechanisms, and the long-horizon run. The complete download is approximately **44.63 MiB**; allow at least **10 GiB** of free disk space for extraction and filesystem overhead.

For the included [compact real FD4 example](examples/README.md):

```bash
.venv/bin/python scripts/import_result_example.py
```

Import this example only once per publication root. If it already exists, skip the import command. Published `.7z` archives are extracted directly into the data layout below; the bundled-example importer is only for the included example.

```text
agent/public_results/runs/<run-directory>/run_meta.json
agent/public_results/runs/<run-directory>/public_manifest.json
```

After importing the example or extracting release data, start the viewer:

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor PYTHON_BIN=.venv/bin/python \
  bash scripts/start_integrated_app.sh
```

Open **http://127.0.0.1:3000**. The page shows **Visitor · read only**. Click **Refresh Run List** after adding data, select a run, and choose **Review Simulation Results**. **Browse Experiment Designs** opens the read-only scenario catalog. No model credentials or `scripts/.env` are needed for browsing; an empty result list means no data have been imported or extracted into the selected publication root.

To browse a private archive that has not already been published as a result copy:

```bash
.venv/bin/python scripts/publish_results.py /path/to/completed/run
```

Release archives already contain their publication manifests and review projections, so they do not require this publication step. See [result publication](docs/public_deployment.md#publish-existing-results) for your own raw archives.

### 3. Understand Startup, Build, and Ports

The integrated script starts the Python backend, checks readiness, and starts the frontend. **Visitor startup builds the production frontend automatically** if the source has changed, and reuses that build on later starts. No separate manual build is required for this route.

| Service | Integrated startup address |
| --- | --- |
| Experiment interface | <http://127.0.0.1:3000> |
| Simulation and operations API | <http://127.0.0.1:8000> |
| Interactive API documentation | <http://127.0.0.1:8000/docs> |

Keep the terminal open. `Ctrl+C` stops the integrated services. If ports are occupied:

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor PYTHON_BIN=.venv/bin/python \
  BACKEND_PORT=8001 FRONTEND_PORT=3001 bash scripts/start_integrated_app.sh
```

Then open `http://127.0.0.1:3001`. For frontend development, explicitly select `SUPPLY_CHAIN_VIEWER_MODE=dev`. For new simulations, continue with [Agent Setup](#agent-setup), which also covers operator access for scripted runs.

### 4. Deploy a Single-Service Visitor Website

After the dependency installation above, build once and start the production server:

```bash
npm --prefix visualization run build
SUPPLY_CHAIN_PLATFORM_MODE=visitor PYTHON_BIN=.venv/bin/python \
  bash scripts/start_public_server.sh
```

This route serves **both the built interface and API at http://127.0.0.1:8000**; there is no separate frontend on port 3000. Unlike integrated startup, this script expects an existing build, so rebuild after frontend source changes. Reuse the same publication root as your imported data.

The default bind address is local-only. For an external website, place this service behind an HTTPS reverse proxy, or choose an appropriate `BACKEND_HOST` for your deployment. See [production deployment](docs/public_deployment.md#production-viewer) for host, data, and writable-runtime settings, and [SSH forwarding](#development-and-troubleshooting) for private remote access.

## Published Experiment Data

[**Simulation Dataset v1.0.0 — download the release**](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/tag/data-v1.0.0) · [Run index CSV](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/RUN_INDEX.csv) · [Download and use guide](docs/data_download.md)

The release provides **46 independent run archives: 34 LLM-driven runs and 12 scripted references**, comprising 1,500 recorded simulation rounds. Each attachment is one `.7z` run archive; choose individual experiments or download the full set. Data are distributed as release attachments and are not included by `git clone` or GitHub's automatically generated **Source code (zip)** download.

| Dataset | Runs | Rounds per run | Conditions |
| --- | ---: | ---: | --- |
| FD1–FD5 enterprise cases | 25 | 20 | AG / Full-MA, RB, NA, NC, SA for each case |
| Bullwhip | 4 | 40 | RB, AG, AG-OC, SA |
| Herding | 8 | 40 | RB, AG, AG-OC, SA; peer-visible and peer-hidden |
| Cobweb | 5 | 40 | Three RB calibrations, AG, AG-OC |
| Commons | 3 | 40 | RB, AG, AG-OC |
| Sequential shocks | 1 | 200 | AG |

For an example of the released data, download [FD4 AG](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/download/data-v1.0.0/FD4_Staffing-Recovery__AG__run_7c72e4af2bfe.7z). Use **7-Zip** (`7zz`, or `7z` on installations using that command) to extract the archive. The [download guide](docs/data_download.md) provides exact commands, all 46 file links, checksum verification, and the required directory layout. The checksums in [data-v1.0.0.sha256](docs/datasets/data-v1.0.0.sha256) are the SHA-256 digests recorded by the GitHub Release API.

Keep the original `run_id`, directory contents, manifest, and projections together. Descriptive directory prefixes identify cases, conditions, and variants in the viewer. The guide also records the single missing original observer snapshot in the Commons RB archive.

## Scenarios

| Scenario family | Simulation focus |
| --- | --- |
| Supply-chain topologies | Firm relationships and transaction flow through configurable networks |
| [Beer game / bullwhip](docs/scenarios/bullwhip.md) | How local orders propagate into upstream demand variability |
| [Cobweb](docs/scenarios/cobweb.md) | Delayed supply decisions and market-price feedback |
| [Herding](docs/scenarios/herding.md) | How peer visibility changes firm decisions and collective behavior |
| [Commons](docs/scenarios/commons.md) | Resource extraction, renewal, and shared-resource pressure |
| Long-horizon evolution | Continued operation and responses to scheduled external changes |
| FD1: order selection | Market coverage and selection of profitable, fulfillable orders under limited acceptance capacity |
| FD2: material shortage | Supplier, quantity, and logistics choices for replenishment |
| FD3: capacity bottleneck | Production planning and capacity allocation |
| FD4: staffing shortage | Recruitment, production recovery, and order fulfillment |
| FD5: working-capital pressure | Expansion decisions, cash protection, and operating commitments |

Each economic guide explains the phenomenon, implemented mechanism, exact experiment conditions, metrics, and interface launch steps. The [configuration guide](docs/configuration.md) also covers FD1–FD5, topology presets, and the 200-round external-shock experiment.

The scenario registry is [config/simulation_preset_config.py](config/simulation_preset_config.py). Select a preset in the interface, or use `SIMULATION_SCENARIO_ID` for direct runs. The default preset is `architecture_linear_chain_short`.

Single-enterprise cases use fixed JSON setup actions to establish their starting conditions before Agent or scripted decisions begin. These setup actions are distinct from the scripted decision controller. See [configuration](docs/configuration.md) for the configuration structure.

Each FD case provides the following comparison conditions in its existing scenario card:

| Condition | Decision arrangement |
| --- | --- |
| Full / MA | Enterprise Analyst and functional department decisions with coordination |
| NA | Department decisions and coordination, with neutral context in place of model diagnosis |
| NC | Analyst and department decisions using authorized facts, without additional shared coordination or department messages |
| SA | A single enterprise LLM task performs diagnosis and produces all enabled functional decisions |

Bullwhip and peer-visible/hidden herding also provide SA conditions under their C3 operating regimes. See [matched experiments](docs/experiments.md) for exact configuration inheritance, retries, batch controls, and evaluation commands.

## Agent Setup

Use this sequence to execute experiments on your own installation. Complete [dependency installation](#1-download-and-install) first. **C1 / Fixed Rule Script conditions can skip Step 1**; Agent conditions, including Full/MA, NA, NC, and SA, require a model service.

### 1. Configure a Model Connection

The runtime uses the **Claude Agent SDK** and its bundled Claude Code CLI. The endpoint must support this runtime's request and tool-use protocol. Use a compatible model service directly, or start and configure a separate translation/routing gateway for your provider. A standalone OpenAI Chat Completions URL is not sufficient by itself. The platform startup scripts do not install or start an external model gateway.

Create a local settings file if you do not already have one:

```bash
if [ ! -f scripts/.env ]; then cp scripts/.env.example scripts/.env; fi
```

Edit `scripts/.env` in a text editor and supply real values:

```dotenv
ANTHROPIC_BASE_URL=https://your-compatible-model-gateway.example
ANTHROPIC_MODEL=your-model-or-gateway-route
ANTHROPIC_AUTH_TOKEN=your-gateway-or-service-credential
```

| Setting | What to supply |
| --- | --- |
| `ANTHROPIC_BASE_URL` | Base URL of the model service or running gateway, separate from the simulation API and viewer addresses |
| `ANTHROPIC_MODEL` | Model ID or routing name accepted by that service/gateway |
| `ANTHROPIC_AUTH_TOKEN` | Credential accepted by that endpoint; `ANTHROPIC_API_KEY` is also supported |

For a separately configured gateway, its upstream provider URL, provider key, and model mapping must also be valid in the gateway's own configuration. The credential supplied to the gateway may differ from its upstream provider key. Model keys are configured on the operator host, not in the browser's **Operator access token** field. Keep `scripts/.env` local; it is ignored by Git.

### 2. Enable Operator Mode and Check the Connection Settings

If a visitor server is already using the same ports, stop it with `Ctrl+C`. In the terminal that will start the operator server:

```bash
export SUPPLY_CHAIN_PLATFORM_MODE=operator
export SUPPLY_CHAIN_OPERATOR_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Operator access token (copy into the browser): %s\n' "$SUPPLY_CHAIN_OPERATOR_TOKEN"
```

Copy the printed token for Step 3. It authorizes this installation and is **separate from the model API credential**. Mode and operator token must be supplied through the process environment; entries in `scripts/.env` cannot enable operator mode. Reuse the same exported token in this terminal while the server runs.

For Agent experiments, now run the configuration diagnostic in the **same terminal**, after enabling operator mode:

```bash
PYTHON_BIN=.venv/bin/python bash scripts/check_agent_cli_env.sh
```

Check for `Agent Environment config: complete`, the intended model and gateway, and an executable bundled CLI with a successful version check. This diagnostic inspects configuration and runs the local CLI version command; it does not make an upstream model request or verify provider quota. If you previously exported different `ANTHROPIC_*` or `AGENT_GATEWAY_BASE_URL` settings, clear/update those values before checking your `.env` configuration.

Start the operator deployment with the production viewer:

```bash
SUPPLY_CHAIN_VIEWER_MODE=production PYTHON_BIN=.venv/bin/python \
  bash scripts/start_integrated_app.sh
```

This command uses the exported operator mode/token and builds or reuses the frontend. Scripted runs use the same operator startup but can skip the model diagnostic and model configuration.

### 3. Unlock Controls and Launch an Experiment

1. Open **http://127.0.0.1:3000** and choose **Browse Experiment Designs** to enter **Experiment Operations**.
2. Enter the token printed in Step 2 into **Operator access token**, then select **Unlock controls**. The access indicator changes to **Operator** and scenario cards offer **Configure Startup**. An operator deployment also appears read-only until the browser is unlocked; a visitor deployment has no unlock form.
3. Choose **Enterprise Cases** or **Multi-Enterprise Scenarios**, open the relevant card, and select the exact **Experiment / ablation condition**. Choose C1 / Fixed Rule Script for a model-free run, or the intended Agent condition when your model service is ready.
4. Review the configuration and select **Run now**. Follow **Task Status** and **Run Details**, then open the finished archive using **Review Results**. FD cases also provide **Automatic batch** for independent serial runs.

The browser holds the operator token in memory; reload or **Lock experiment controls** clears it. A token cannot turn a visitor server into an operator server: the host must restart with the explicit operator settings. On a hosted visitor site, use **Request execution access** if the host provides that link, or configure your own installation.

Integrated server startup preserves existing shell settings. Each new worker refreshes explicitly configured, non-empty model settings from `scripts/.env`; already-running workers retain their environment. After changing model settings, restart the platform with consistent shell/file values and create a new job. Keep the model service available throughout the run.

Skills and structured action examples live in [agent/.claude/skills/](agent/.claude/skills/). See [firm agents and Skills](docs/agents.md), [matched experiments](docs/experiments.md), and the four [economic guides](docs/configuration.md#scenario-guide-index) for scenario-specific instructions.

<details>
<summary><strong>Optional: run a two-round scripted smoke test through the API</strong></summary>

With an operator deployment running, export the **same server operator token** in a second terminal and execute these commands from the repository root:

```bash
JOB_ID=$(curl -fsS -X POST http://127.0.0.1:8000/operations/jobs \
  -H "Authorization: Bearer $SUPPLY_CHAIN_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' \
  --data '{"scenario_id":"beer_game_scripted","planned_total_steps":2,"config_overrides":{"simulation":{"service_total_steps":2,"agent_run_steps":2}}}' \
  | .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["data"]["job_id"])')

curl -fsS -X POST "http://127.0.0.1:8000/operations/jobs/$JOB_ID/run" \
  -H "Authorization: Bearer $SUPPLY_CHAIN_OPERATOR_TOKEN" \
  -H 'Content-Type: application/json' --data '{}'

curl -fsS "http://127.0.0.1:8000/operations/jobs/$JOB_ID" \
  -H "Authorization: Bearer $SUPPLY_CHAIN_OPERATOR_TOKEN"
```

Job dispatch is asynchronous. Repeat the final request until it reports `status: completed` and `completed_steps: 2`. This is a **scripted** check of job creation, execution, checkpointing, and operations storage; it does not validate the model connection. Use your selected backend port if different from 8000.

</details>

## Experiment Interface

The English interface connects the experiment catalog to round-level evidence and scenario-specific analysis. Its compact toolbar keeps run, case, controller, and display choices together; layouts adapt to desktop, tablet, and narrow screens.

### 1. Find and Compare Archived Runs

[![Results browser with scenario filters, selected-run metadata, highlighted experiments, and archive access.](docs/assets/results-browser.png)](docs/assets/results-browser.png)

Select a scenario family and run, inspect its configuration and completion status, then open its results. The archive supports filtering, selecting multiple runs, comparison summaries, record exports, and available public archive downloads.

### 2. Inspect Enterprise Decisions and Operating Outcomes

[![Single-enterprise round review showing operating evidence, finance, production, capacity, inventory, order tracking, and fixed round navigation.](docs/assets/enterprise-round-review.png)](docs/assets/enterprise-round-review.png)

The screenshot shows an FD4 NC run. **Round View** combines enterprise state, executive analysis, department objectives, actions, feedback, and operating trends. Switch between process trends and round-level state; the fixed bottom round selector remains accessible while scrolling. Expand evidence details to inspect output quality, execution receipts, units, denominators, and source records.

### 3. Follow Inter-Firm and Economic Dynamics

[![Bullwhip scenario analysis with request trends, transmission ratios, a round-level heatmap, and display controls.](docs/assets/bullwhip-analysis.png)](docs/assets/bullwhip-analysis.png)

**Propagation Review** follows current-round order and production flows; **Global Review** summarizes system trajectories; **Operations Review** connects operating quantities and business records. **Scenario Analysis** provides dedicated views for bullwhip, cobweb, herding, commons, and long-horizon evolution. In the bullwhip view shown here, switch between effective propagation and raw requests, and use absolute, log-scale, or relative-peak displays to inspect different magnitudes.

Charts open in viewport-sized dialogs that adapt to window resizing, with an explicit **Close chart** button and Escape support. Empty flow views explain the absence of recorded flow. Loading indicators, local retry controls, and archive error messages keep the current state visible while data or view modules load.

### 4. Browse Designs and Manage Authorized Experiments

[![English Experiment Operations page showing visitor access, service status, recent results, and enterprise case configurations.](docs/assets/experiment-operations.png)](docs/assets/experiment-operations.png)

Visitors can inspect configurations and existing results. A separately enabled operator deployment unlocks launch, stop, recovery, and automatic-batch controls. Each batch item retains its own run environment and files, and the server scheduler continues after the browser closes.

**Efficient review:** production builds are reused until viewer source changes; compact chart projections, bounded concurrent reads, request cancellation, and caching reduce repeated archive work. View modules and detailed records load on demand. See [deployment and publication](docs/public_deployment.md) for preparing result copies and [interface and artifacts](docs/interface_and_artifacts.md) for the underlying files.

## Data and Databases

| Local location | Purpose |
| --- | --- |
| `agent/public_results/runs/` | Reviewed publication copies available to visitors |
| `agent/workspace_jobs/` | Generated jobs, configuration snapshots, firm artifacts, and checkpoints |
| `agent/runtime/` | Default operations database, logs, download caches, and runtime recovery records |
| `agent/workspace_jobs/_operations/` | Legacy operations database location retained when already present |
| `agent/workspace_multi/` | Active multi-enterprise working state |
| `agent/logs/` | Runtime and Agent SDK diagnostic logs |
| `agent/simulation_runs/` | Legacy local archives, when present |

These generated locations are ignored by Git. The source repository includes code, Skills, setup templates, and the compact FD4 example. The [46-run paper dataset](https://github.com/gogogoGoya/SupplyChainAgent-OrgEconSim/releases/tag/data-v1.0.0) is distributed as separate release attachments.

The [database/](database/) package retains **SQLAlchemy business models and repository access** for enterprise, product, and order records. This optional ORM database is separate from the SQLite operations mirror. Initialize its tables with:

```bash
.venv/bin/python -c 'from database.db_manager import init_db; init_db()'
```

The default ORM store is `local_orm.sqlite`. Set `DATABASE_URL` to use PostgreSQL. See [database storage](docs/database.md) for details.

## Project Map

| Directory | Responsibility |
| --- | --- |
| `agent/` | Enterprise runtimes, Agent Skills, scripted decisions, and setup templates |
| `config/` | Scenario registry, enterprise specifications, and runtime policies |
| `core/`, `enterprise/` | Economic state, action execution, and department business operations |
| `coordination/`, `message/` | Department communication and coordination protocols |
| `network/` | B2B exchange, proposals, orders, and firm relationships |
| `simulate/` | FastAPI simulation service and environment adapters |
| `runtime/`, `persistence/` | Job orchestration, archives, checkpoints, and storage projections |
| `database/` | Optional ORM models and repository access |
| `visualization/` | React/Vite experiment interface and analysis views |
| `scripts/`, `test/` | Startup tools, environment checks, and regression tests |

## Documentation

| Guide | Start here to understand |
| --- | --- |
| [Visitor and operator access](docs/visitor_mode.md) | Browse results or configure an authorized experiment host |
| [Public deployment and publication](docs/public_deployment.md) | Prepare reviewed result copies and host the production viewer |
| [Matched experiments](docs/experiments.md) | Understand MA/NA/NC/SA conditions, serial batches, and evaluation exports |
| [Published dataset downloads](docs/data_download.md) | Download, check, extract, and browse the 46 paper runs |
| [Compact example](examples/README.md) | Import the small real FD4 archive for an initial walkthrough |
| [Architecture and round lifecycle](docs/architecture.md) | How decisions, transactions, and persistent processes advance |
| [Scenarios and configuration](docs/configuration.md) | FD1–FD5, topology, long-horizon events, and configuration structure |
| [Bullwhip](docs/scenarios/bullwhip.md) · [Herding](docs/scenarios/herding.md) · [Cobweb](docs/scenarios/cobweb.md) · [Commons](docs/scenarios/commons.md) | Economic meaning, simulation design, metrics, and exact launch steps |
| [Firm agents and Skills](docs/agents.md) | How enterprise analysis and department decisions are organized |
| [Database storage](docs/database.md) | How operations storage and optional ORM storage differ |
| [Interface and artifacts](docs/interface_and_artifacts.md) | Where to inspect outcomes and find generated files |

## Development and Troubleshooting

Run verification locally after installing the project dependencies. Backend tests, frontend checks, and optional browser tests remain available through the commands below.

```bash
# Run backend regression tests.
.venv/bin/python -m pytest test/

# Check and build the experiment interface.
npm --prefix visualization run lint
npm --prefix visualization test
npm --prefix visualization run build

# Install the browser once, then run visitor and failure-recovery checks.
(cd visualization && npx playwright install chromium)
PYTHON_BIN=.venv/bin/python npm --prefix visualization run test:browser
```

| Symptom | What to check |
| --- | --- |
| `vite: not found` | Run `npm --prefix visualization ci` from the repository root. |
| Missing Python backend dependencies | Install `requirements.txt`; point `PYTHON_BIN` to the project virtual environment. |
| No runs listed | Import the compact example, extract release data into `agent/public_results/runs/`, or publish a raw archive; click **Refresh Run List**. |
| `unzip` cannot open a release file | Release data use `.7z`; install 7-Zip and follow the [download guide](docs/data_download.md). |
| A chart has no flow | Check the selected round and external-flow filters; a round may have no recorded order or production flow. |
| A view fails after a release update | Use the visible retry or reload action to load the current frontend assets. |
| Data cannot be loaded | Check backend connectivity, the publication manifest, and the displayed archive availability message. |
| Agent configuration appears missing | Enable operator mode and export its token **before** running the diagnostic in the same terminal; check for conflicting shell model settings. |
| Agent gateway or authentication error | Check the service/gateway, model route, credential, and provider quota. The local configuration diagnostic does not send a model request. |
| No operator token input, or **Run now** stays disabled | A visitor server must be restarted in operator mode. On an operator server, unlock controls with its operator token, not a model API key. |
| Interface opens on a different port | Integrated startup uses frontend 3000/backend 8000; `start_public_server.sh` serves both on 8000. Check your selected port overrides. |
| Port already in use | Choose available `BACKEND_PORT` and `FRONTEND_PORT` values. |

For a remote SSH session, keep the startup script running inside `tmux`. For integrated startup, forward the frontend port to access the interface locally:

```bash
ssh -L 3000:127.0.0.1:3000 user@server
```

Then open <http://127.0.0.1:3000> on your local machine. Use the selected frontend port if it differs from the default. For `start_public_server.sh`, forward port 8000 instead and open `http://127.0.0.1:8000`.

## License

Released under the [MIT License](LICENSE).
