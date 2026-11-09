# Experiment design, execution, and evaluation

[Scenario catalog](configuration.md) · [Operator setup](visitor_mode.md#enable-experiment-execution) · [Results review](interface_and_artifacts.md)

## Choose the comparison

| Group | Question | Decision arrangement |
| --- | --- | --- |
| C1 / RB | Does the configured environment reproduce its reference mechanism under rules? | Fixed Rule Script |
| C2 | Can constrained Agent decisions reproduce the selected classical mechanism? | Organizational Agent Skills with mechanism-oriented policies |
| C3 / AG-OC | How do profit, cash, service, and risk objectives reshape behavior? | Organizational Agent Skills under operating objectives |
| S0 | Can an enterprise diagnose and address the designated operating constraint? | Controlled FD case; condition depends on selected preset |
| Full / MA | How does the complete organizational FD controller perform? | Analyst, departments, and shared coordination |
| NA | What changes without dynamic enterprise diagnosis? | Department decisions with neutral diagnostic context |
| NC | What changes without additional shared coordination? | Analyst and authorized department facts; shared projections/messages removed |
| SA | What changes when enterprise functions are consolidated? | Unified enterprise LLM decision task |
| E1 | Can the supply chain continue operating across successive external changes? | Long-horizon adaptive enterprise decisions |

Condition labels identify the experiment design, not a guaranteed ordering of results. The four [economic guides](configuration.md#scenario-guide-index) explain their matching scenario IDs and mechanism-specific evidence.

## Launch one job

1. Start an [operator deployment](visitor_mode.md#enable-experiment-execution) and unlock the browser controls. C1 needs no model configuration; Agent conditions need your model service.
2. Open **Experiment Operations**, then **Multi-Enterprise Scenarios** or **Enterprise Cases**.
3. On the relevant card, choose **Configure Startup**, then the exact **Experiment / ablation condition**.
4. Review the resolved group, rounds, products, target firms, and mechanism/architecture settings. **Modify Configuration** permits temporary changes to editable fields.
5. Click **Run now**. Track the run through **Task Status**, **Run Details**, and the job history; select its completed archive using **Review Results**.

A manual run gets its own job, configuration, worker, run ID, and output directory. Only one Agent Skill job is admitted at a time by the interface; independent serial batches automate that sequence.

Visitors can inspect scenario configuration and public results but cannot launch or change conditions. Access is enforced by the backend as well as the UI.

## FD1–FD5 matched ablation design

For each N from 1 to 5, use:

```text
firm_ablation_fdN_ma     Full organizational reference
firm_ablation_fdN_na     No dynamic Analyst diagnosis
firm_ablation_fdN_nc     No additional shared coordination
firm_ablation_fdN_sa     Unified enterprise decision
```

All four use 20 rounds: **R0–R9 construction; R10–R19 decision window**. The current matched protocol is `fd_ablation_2026_10_26_v1`. It fixes the designated case construction, economic rules, five-round history, execution adapter, three attempts per task, and 300 seconds per attempt. The architecture/information component is selected by the arm.

Base `single_case_*` entries and their historical configuration selectors remain available. For a matched comparison, select the dedicated Full/MA, NA, NC, and SA entries in **Experiment / ablation condition**, rather than relying on a historical selector or a previously created job.

The Analyst is advisory in this protocol. Fixed department opportunities, audit-only consistency checks, and disabled semantic rescue help make accepted business effects attributable to the recorded decision/repair path. FD2/FD4 apply their configured case refinements when resolving the matched setup; the resolved snapshot is the comparison reference.

## Economic architecture comparisons

Bullwhip and visible/hidden herding provide dedicated SA entries. Their operating regimes are loaded from frozen C3 snapshots under [config/firm_ablation_baselines/](../config/firm_ablation_baselines/), with manifest hashes. The original historical archive is not needed to resolve these portable configurations.

| SA condition | Intended organizational reference |
| --- | --- |
| `firm_ablation_bullwhip_sa` | C3 bullwhip operating regime, associated with `beer_game_profit_objective` |
| `firm_ablation_herding_visible_sa` | C3 peer-visible regime, associated with `herding_peer_visible_profit_objective` |
| `firm_ablation_herding_hidden_sa` | C3 peer-hidden regime, associated with `herding_no_peer_visibility_profit_objective` |

The optional economic `*_ma` presets use a legacy modified C2 development protocol. They are not automatically matched controls for these SA runs. For a newly executed C3/SA comparison, compare the resolved economic settings, visibility, retry contract, and execution protocol with the frozen baseline. A scenario label alone does not establish equivalence across software versions.

Cobweb and commons currently have C1/C2/C3 configurations but no dedicated SA condition. Their guides describe the calibration and objective comparisons that are available.

## Independent serial FD batches

In an FD case dialog, enable **Automatic batch**, select Full/MA, NA, NC, and/or SA, then set **Repeats per group**. Click **Start automatic batch**.

For each repeat the server executes selected groups in displayed order. Every item receives its own frozen defaults, job, worker process, run ID, workspace, and files; temporary single-run form edits are not applied to the batch items. The previous worker must exit before the next starts.

The scheduler continues after the browser closes. Keep the backend, server, model gateway, and sufficient provider quota available. A final failure, stopped worker, or incomplete final-round completion pauses the batch. Existing within-task feedback attempts still occur before a task is considered final.

**Resume pending items (skip failures)** leaves completed items intact. Rerun a failed condition as a new job if needed. A restarted scheduler can reattach to a live current worker; a batch reservation prevents an unrelated manual launch from interrupting its sequence. Batch records live under `agent/workspace_jobs/_batches/`.

These UI batches are for FD groups. Launch economic conditions separately. The API provides `GET/POST /operations/batches` and `POST /operations/batches/{batch_id}/pause` or `/resume`.

## Stop and recovery

**Abort Simulation** requests a controlled stop; the worker records the stop at its supported boundary. After an abnormal process exit, an operator can use **Reconcile exited workers** to refresh task status.

E1 runs with a sealed complete-round checkpoint and remaining rounds can expose **Resume Run**. Restore the private checkpoint and its corresponding state rather than attempting to resume from a public display ZIP. Public archives omit executable checkpoint material.

An unknown business receipt quarantines the environment session. Let a pending operation drain and follow sealed-checkpoint recovery; ordinary model-output retry is not a rollback mechanism. See [execution semantics](architecture.md#concurrent-decisions-and-shared-state).

## Prepare a frozen request manifest

This command records configurations, source/Skill hashes, and job-creation requests without starting simulations:

```bash
.venv/bin/python scripts/prepare_fd_ablation_batch.py \
  --batch-id matched-fd \
  --families fd2 \
  --repeats 5 \
  --model-label 'actual-server-model' \
  --output .local_artifacts/matched-fd/manifest.json
```

Without `--families`, all five FD cases are included. The model label documents the intended route; it does not configure the gateway or verify the provider's actual model identity.

## Evaluate and export

```bash
.venv/bin/python scripts/export_firm_ablation_evaluation.py \
  /path/to/full-ma-run /path/to/sa-run \
  --output .local_artifacts/matched-fd/export
```

| Evidence dimension | What to compare |
| --- | --- |
| Run integrity | Planned/completed rounds, expected firms, stage/window coverage, and required snapshots |
| Decision quality | Recorded tasks, attempts, valid final outputs, timeouts, and unresolved failures |
| Repair | Retries, linked replacement executions, safe withdrawals, and unresolved/unknown receipts |
| Business execution | Accepted/executed/rejected actions and their recorded denominators |
| FD outcomes | Diagnosis, procurement, capacity, staffing, inventory, liquidity, production, and fulfillment relevant to the case |
| Economic outcomes | Family-specific mechanism indicators plus firm operating results |
| Time | Whole-run wall-clock interval and separately recorded phase/attempt durations |

FD exports distinguish the R9 accepted-order cohort from new orders and calculate decision-window finance from R19 minus R9. Compare the same cohort and denominator. Recruitment batches are not onboarded people; an accepted production plan is not completed output.

Whole-run duration includes scheduling and waiting. Summed phase durations can overlap and do not automatically equal wall time. For repeated experiments, retain the block's source/Skill identity, actual model route, economic setup, execution protocol, and window/cohort definitions.

Unresolved ordinary output/business failures can be recorded while the run continues. Permanent authentication, permission, model-configuration, account-quota, and fatal tool-channel failures stop the job; a batch then pauses. Private credential diagnostics remain outside publication artifacts, while public error summaries use fingerprints.

## Regression checks

```bash
.venv/bin/python -m pytest test/test_fd_matched_ablation.py \
  test/test_firm_architecture_experiment.py test/test_fd_batch.py \
  test/test_model_failure.py
```

These tests use controlled model fixtures and the business engine to verify execution and recording contracts. Use the [operator connectivity check](../README.md#agent-setup) before a real Agent experiment. For browser/access checks, see [deployment verification](public_deployment.md#verification).
