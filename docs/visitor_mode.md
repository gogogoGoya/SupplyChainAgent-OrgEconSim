# Visitor and operator access

[Quick start](../README.md#quick-start) · [Public deployment](public_deployment.md) · [Experiment execution](experiments.md)

## Choose the deployment mode

| Deployment and browser access | Browse published results | Inspect scenario defaults | Execute/control experiments |
| --- | --- | --- | --- |
| Visitor deployment | Yes | Yes | No |
| Operator deployment, browser locked | Yes | Yes | No |
| Operator deployment, valid operator token | Yes; private job records are also available | Yes | Yes |

A visitor deployment is the default. It does not load `scripts/.env`; existing host model variables do not unlock execution. The badge **Visitor · read only** identifies this browsing state. Scenario cards offer **View Configuration** rather than **Configure Startup**. The backend rejects write requests as well as locking browser controls.

## Browse without a model account

Install the [dependencies](../README.md#quick-start), then load [published release data](data_download.md) or import the compact example below. After loading data, start the platform:

```bash
.venv/bin/python scripts/import_result_example.py
SUPPLY_CHAIN_PLATFORM_MODE=visitor bash scripts/start_integrated_app.sh
```

Open `http://127.0.0.1:3000`. Choose a run and **Review Simulation Results**, or open **Browse Experiment Designs** to inspect the scenario catalog. If importing/publishing while the viewer is open, click **Refresh Run List**.

The example importer uses a new `fd4-review` destination and refuses to overwrite an existing copy. For full records use [publication tools](public_deployment.md#publish-existing-results).

## Enable experiment execution

On your own installation, follow the full [model and operator setup](../README.md#agent-setup). The required order is:

1. Install dependencies from the [README](../README.md#1-download-and-install).
2. For Agent conditions, create `scripts/.env` from the example if needed and configure your SDK-compatible model service/gateway, model ID, and credential. Start an external gateway separately if your provider requires one. Scripted conditions can skip model configuration.
3. Stop a visitor service using the same ports, then enable operator mode and generate a separate access token:

```bash
export SUPPLY_CHAIN_PLATFORM_MODE=operator
export SUPPLY_CHAIN_OPERATOR_TOKEN="$(.venv/bin/python -c 'import secrets; print(secrets.token_urlsafe(32))')"
printf 'Operator access token (copy into the browser): %s\n' "$SUPPLY_CHAIN_OPERATOR_TOKEN"
```

4. For Agent conditions, run `PYTHON_BIN=.venv/bin/python bash scripts/check_agent_cli_env.sh` in this same terminal **after** the exports above. The diagnostic checks configuration and the local bundled CLI; it does not call the upstream model service.
5. Start the platform using the exported operator settings:

```bash
SUPPLY_CHAIN_VIEWER_MODE=production PYTHON_BIN=.venv/bin/python \
  bash scripts/start_integrated_app.sh
```

6. Open **Browse Experiment Designs** / **Experiment Operations**, enter the printed value of `SUPPLY_CHAIN_OPERATOR_TOKEN`, and select **Unlock controls**. Scenario cards now offer **Configure Startup**, and authorized job controls become available. The page remains read-only until unlocked.
7. Choose a [scenario guide](configuration.md#scenario-guide-index) or [FD experiment](experiments.md#fd1fd5-matched-ablation-design), review its configuration, and use **Run now**.

The access token authorizes control of this installation. It is distinct from a model API key, must contain at least 32 characters, and is held only in browser memory. Reloading the page clears it. **Lock experiment controls** discards the browser token immediately.

Mode and operator token are process-environment settings; entries in `scripts/.env` cannot enable execution or replace that token. Operator startup loads model settings separately. A visitor server cannot be unlocked by entering a token: the host must explicitly enable an operator deployment.

For automation, send `Authorization: Bearer $SUPPLY_CHAIN_OPERATOR_TOKEN` on write requests. API examples are in the [README](../README.md#quick-start); interactive API documentation is at `http://127.0.0.1:8000/docs` by default.

## Offer an access-request link

A public host can display its existing contact/application process:

```bash
SUPPLY_CHAIN_ACCESS_REQUEST_URL=https://example.org/request-access \
  SUPPLY_CHAIN_PLATFORM_MODE=visitor bash scripts/start_integrated_app.sh
```

The website provides a link, not automatic account creation or approval. HTTP(S) and mailto links are accepted. Approved execution uses a separately enabled operator installation or an explicitly reconfigured host.

## Separate public viewing from execution

Public results are manifest-backed copies under `agent/public_results/` or the selected publication root. Private job archives, live engine state, process details, and credential diagnostics require operator access. Public reads do not restart workers or reconcile batch schedulers.

An operator token grants host-level control, not separate per-user quotas or tenant isolation. A shared operator host uses that host's configured model account. Use HTTPS for remote operator access and separate public/private processes, runtime folders, and databases when deploying both services.

Before changing an operator installation to visitor mode, stop its active workers and batches through their existing controls. Restarting a web process does not itself terminate detached simulation work. See [management and recovery](experiments.md#stop-and-recovery).

## Viewer startup and recovery

Visitor startup serves a production build and reuses it while its source fingerprint matches. Use `SUPPLY_CHAIN_VIEWER_MODE=dev` only for frontend development. Both modes enforce the same access boundary.

A view displays loading state while fetching modules or archives. Local errors offer retry/reload actions; fatal empty-root errors expose an independent reload screen. See [loading and recovery](interface_and_artifacts.md#loading-and-recovery) for the corresponding buttons and [deployment](public_deployment.md) for hosting and result preparation.
