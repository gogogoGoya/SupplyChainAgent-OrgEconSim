# Publish results and deploy the viewer

[Visitor/operator access](visitor_mode.md) · [Results interface](interface_and_artifacts.md) · [Storage](database.md)

## Publish existing results

Run from the repository root:

```bash
.venv/bin/python scripts/publish_results.py /path/to/completed/run
```

The tool reads the original archive and creates a new publication under `agent/public_results/runs/`. It does not run a model or modify the original archive. Existing destinations are not overwritten. Set `SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT` for a different root, or use `--destination` with a new directory under that root's `runs/` category.

A publication includes `public_manifest.json`, file hashes, and derived compact review/trend projections. Only manifest members are served and included in public downloads. New files dropped into the directory do not automatically become public.

The exporter excludes executable checkpoints, SDK attempt directories, host configuration, private diagnostics, databases, and escaping links. It removes structured credentials and known credential patterns. Review free-text content before publishing because automatic filters cannot establish that every arbitrary message is suitable for release.

For an initial walkthrough, import the [compact real FD4 example](../examples/README.md):

```bash
.venv/bin/python scripts/import_result_example.py
```

Full historical archives are distributed separately from the source repository. Publishing/importing on one host does not install the data on another host. After adding records, choose **Refresh Run List** in the viewer.

## Check and publish a gallery

Select explicit archive paths and check their recorded display coverage before copying:

```bash
.venv/bin/python scripts/prepare_result_gallery.py /data/run_A /data/run_B \
  --check-only --report /data/review/gallery-check.json
.venv/bin/python scripts/prepare_result_gallery.py /data/run_A /data/run_B \
  --report /data/review/gallery-published.json
```

The check covers declared enterprises/rounds, end-of-day observations, invalid JSON, and six formal department snapshots. It distinguishes FD `prewarm_end_of_day` from formal `end_of_day` records. The report describes snapshot availability separately from decision quality or matching experiment conditions.

The full selection is checked before publication. Each run receives a separate destination and manifest. After an interrupted copy, completed publication directories remain available; select only the remaining source archives on the next invocation. Keep reports outside the public root.

`--destination-root` selects another publication root. Start the viewer with the same `SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT` value.

## Add compact projections to an existing publication

New publications derive compact evidence, execution-quality, and chart projections. To prepare them for an older publication without copying all large business files again:

```bash
.venv/bin/python scripts/enrich_result_review.py /data/original/run \
  /data/public/runs/run --output /data/review/run-prepared --install
```

The tool validates the original metadata hash, prepares versioned projection members, and switches the manifest after preparation. Its output directory must be outside both archives. Replaced projection versions remain available; the manifest selects the active version. `--views-only` restricts the enrichment to chart projections.

The projections retain source coverage, recorded denominators, and available action-parent links. They do not invent absent model health records or reconstruct missing lineage from similar action names. The interface reports those availability differences.

## Local integrated viewer

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor bash scripts/start_integrated_app.sh
```

Defaults are frontend `http://127.0.0.1:3000` and backend `http://127.0.0.1:8000`. The script checks readiness and starts a production viewer for visitor mode. Source fingerprints determine whether a rebuild is needed. `SUPPLY_CHAIN_VIEWER_MODE=dev` selects frontend development mode; both modes use backend artifact routes.

Use `PYTHON_BIN` to select the interpreter and `FRONTEND_PORT` / `BACKEND_PORT` to avoid occupied ports. In a remote shell, keep the script in `tmux` and forward the frontend port as shown in the [README](../README.md#development-and-troubleshooting).

## Production viewer

Install and build once:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints-tested.txt
npm --prefix visualization ci
npm --prefix visualization run build
```

Then start the single-service visitor host:

```bash
SUPPLY_CHAIN_PLATFORM_MODE=visitor \
SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT=/data/supplychain/public \
SUPPLY_CHAIN_RUNTIME_ROOT=/data/supplychain/runtime \
  bash scripts/start_public_server.sh
```

The API serves the built viewer on port 8000. Use a reverse proxy with HTTPS and proxy `/` to that service. The publication and source folders can be read-only; the runtime folder must be writable for operations storage, logs, quarantine records, and download caches.

| Setting | Purpose |
| --- | --- |
| `PYTHON_BIN` | Python interpreter; public-server script defaults to `.venv/bin/python` |
| `BACKEND_HOST`, `BACKEND_PORT` | Bind address and API/viewer port |
| `SUPPLY_CHAIN_FRONTEND_DIST` | Prebuilt frontend directory; defaults to `visualization/dist` |
| `SUPPLY_CHAIN_PUBLIC_RESULTS_ROOT` | Published result root, containing `runs/` |
| `SUPPLY_CHAIN_RUNTIME_ROOT` | Mutable runtime storage |
| `SUPPLY_CHAIN_ACCESS_REQUEST_URL` | Optional host-provided application/contact link |

Health/access responses and the global badge expose release/protocol identity. The startup scripts fail on a conflicting service rather than silently changing ports. The frontend development server restricts filesystem access to viewer source and dependencies.

## Update, back up, and recover

1. Preserve the current publication roots and take a consistent backup of mutable job/batch/database state before an execution-host update.
2. Update source and dependencies, build the viewer, and restart the intended deployment. Existing browser tabs can select **Reload viewer** to load the new assets.
3. Keep public publications separate from private checkpoints and credential diagnostics. Use the [storage guide](database.md#backups-and-transfer) for their different backup purposes.
4. On an operator host, stop/pause work before shutting down the engine. Closing the browser leaves workers and schedulers running.
5. Use **Reconcile exited workers**, batch resume controls, or **Resume Run** only where the recorded job/checkpoint supports them. Follow the [execution recovery semantics](experiments.md#stop-and-recovery) for an indeterminate receipt.

## Verification

From the repository root:

```bash
.venv/bin/python -m pytest test/test_platform_revision.py \
  test/test_published_results.py test/test_execution_review.py \
  test/test_result_gallery.py test/test_platform_access.py test/test_fd_batch.py -q
npm --prefix visualization run lint
npm --prefix visualization test
npm --prefix visualization run build
npm --prefix visualization run test:dev-boundary
(cd visualization && npx playwright install chromium)
PYTHON_BIN=.venv/bin/python npm --prefix visualization run test:browser
```

Browser checks start isolated visitor services with explicitly synthetic data and no model calls. They verify three viewport sizes, units, denied writes, chart enlargement/closing, and failure recovery for scripts, modules, catalogs, archives, and rendering exceptions. `BROWSER_TEST_OUTPUT` selects screenshot/log output. The [CI workflow](../.github/workflows/platform.yml) runs these checks alongside runtime/access/batch regressions.

Use the [interface guide](interface_and_artifacts.md#loading-and-recovery) to interpret visible empty, loading, and error states. Use [experiment management](experiments.md) for private execution and [the scenario guides](configuration.md#scenario-guide-index) for mechanism-specific runs.
