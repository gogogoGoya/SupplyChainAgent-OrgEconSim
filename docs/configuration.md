# Scenarios and configuration

[README](../README.md) · [Experiment execution](experiments.md) · [Interface guide](interface_and_artifacts.md)

## Scenario guide index

The four economic families have separate guides. Each starts with the economic meaning, then explains the implemented mechanism, conditions, metrics, artifacts, and interface launch steps.

| Economic family | Guide | Experiment Operations card |
| --- | --- | --- |
| Bullwhip | [Demand propagation through a supply chain](scenarios/bullwhip.md) | Bullwhip Effect |
| Herding | [Peer information and synchronized production](scenarios/herding.md) | Herding Effect |
| Cobweb | [Delayed supply and market prices](scenarios/cobweb.md) | Cobweb Model |
| Commons | [Extraction and shared-resource pressure](scenarios/commons.md) | Tragedy of the Commons |

The platform also provides the five FD enterprise cases, topology presets, and long-horizon evolution described below. A scenario family can contain several controller conditions and parameter variants; the family card alone does not identify the full experiment.

## Resolved configuration

[simulation_preset_config.py](../config/simulation_preset_config.py) is the registry. It constructs scenario defaults, then resolves controller conditions and permitted launch-time overrides. Each job stores its resolved configuration so later preset changes do not silently replace that job's setup.

| Configuration area | Responsibility |
| --- | --- |
| `meta`, `experiment_design` | Identity, family, decision regime, experiment group, and purpose |
| `simulation` | Round counts, market mode, products, demand series, concurrency, and mechanism parameters |
| `enterprise_specs` | Firms, roles, supplier connections, cash, inventory, staff, capacity, and products |
| `agent_enterprise_layout` | Enabled department roles and Agent/scripted arrangements |
| `initial_action_batches`, `daily_actions` | Initialization and scheduled business operations |
| `runtime_injection` | Authorized context, information visibility, operating policies, and architecture condition |
| `auto_policy` | Configured automatic behavior |
| `integration_profiles` | Analysis, coordination, persistence, and integration capability profiles |
| `formal_experiment_config` | Declared duration, warmup, comparison role, and evaluation window when provided |

In the interface, open **Configure Startup** and choose **Experiment / ablation condition**. Inspect the configuration before using **Modify Configuration** for permitted temporary changes. Closing the dialog does not rewrite registry defaults. Fixed FD and C3-inherited SA conditions lock their economic fields to preserve their designated comparison setup.

For direct runtime entry points, `SIMULATION_SCENARIO_ID` selects a registered preset; the default is `architecture_linear_chain_short`. The operations UI uses the explicit scenario selected in its request.

List registered identities without running a simulation:

```bash
.venv/bin/python - <<'PY'
from config.simulation_preset_config import get_available_scenarios
for scenario in get_available_scenarios():
    print(scenario['scenario_id'], scenario['experiment_design'].get('experiment_group'))
PY
```

## FD1–FD5: controlled enterprise cases

Each case constructs an operating enterprise in R0–R9 and observes decisions in R10–R19. The business facts are prepared through executable setup actions. A diagnosis should identify the actual constraint rather than treating every case as a need for more production.

| Case | Base scenario ID | Operating question | Evidence to follow |
| --- | --- | --- | --- |
| FD1 | `single_case_01_order_selection` | Can the firm develop sales opportunities and select a profitable, fulfillable order portfolio under limited acceptance capacity? | Market actions, order choices, feasibility, fulfillment, and profit |
| FD2 | `single_case_02_material_shortage` | Can procurement resolve material shortage through suitable suppliers, quantities, and logistics? | Supplier selection, purchase receipts, arrival timing, material stock, and production recovery |
| FD3 | `single_case_03_capacity_bottleneck` | Can production identify and address constrained line capacity with material available? | Line availability, plans, utilization, output, and delivered commitments |
| FD4 | `single_case_04_staff_shortage` | Can staffing and production recover when line assets and material exist but production labor is missing? | Recruitment acceptance/onboarding, plan execution, output, and fill rate |
| FD5 | `single_case_05_cash_pressure` | Can the firm manage fulfillment, inventory, and cash after operating investment? | Cash and spending, new commitments, inventory, output, and service |

The base `_scripted` variants provide rule-based policies. Matched organizational conditions use `firm_ablation_fdN_ma`, `_na`, `_nc`, and `_sa`. Their case-specific setup may include validation refinements, so use the full matched MA as the reference for that block. [Experiments](experiments.md#fd1fd5-matched-ablation-design) explains the conditions and workflow.

In the website, use **Experiment Operations → Enterprise Cases → Configure Startup**. Select the condition from the existing case dialog; no separate ablation card is required.

## Long-horizon evolution

`long_horizon_evolution` is an E1, 200-round, four-tier supply-chain run. It examines continued operation and responses to observable external changes, independently of the C1/C2/C3 mechanism comparisons.

| Event round | External change relative to baseline |
| --- | --- |
| R40 | External material prices ×1.25 |
| R80 | Manufacturing conversion costs ×1.80 |
| R120 | External logistics costs ×2.40 |
| R160 | Partial relief: material ×1.10, conversion ×1.12, logistics ×1.55; demand ×0.86 and customer price ×0.96 |

Factors for other categories persist until updated. Agents see current/recent events and active factors; future event content is hidden by policy. Read phase-level operational responses alongside confirmed orders, delivery, cash, inventory, and costs.

Launch from **Multi-Enterprise Scenarios → Continuous Evolution → Configure Startup**, keep 200 rounds, and use **Run now** after operator/model setup. Completed-state checkpoints support the conditional **Resume Run** workflow described in [experiment management](experiments.md#stop-and-recovery). Review the result in **Scenario Analysis → Continuous Evolution**.

## Topology and exploratory presets

`architecture_linear_chain_short`, `architecture_branching_assembly_short`, and `architecture_mesh_multi_source_short` exercise different firm networks. Their focus is connection structure and executable transactions, not reproduction of one of the four economic effects. Topology archives appear in the results browser; these presets are not separate cards in the current five-family economic launcher. They can be selected by exact ID through the operations API or a direct runtime entry point.

The registry also contains historical baselines, direct-distribution, credit, parameter explorations, and compatibility aliases. Select those by exact ID and retain their resolved configuration when using them. The main economic guides identify the formal and calibration entries.

## Add or modify a scenario

1. Declare enterprise IDs, enabled functions, products, recipes, and compatible supplier relationships.
2. Choose the market mode and its mechanism parameters.
3. Define initialization, scheduled events, decision/visibility policies, and controller arrangement.
4. Give the configuration a unique identity and experiment-design metadata.
5. Check product targets, initialization, complete-round recording, and business receipts with a scripted or fixture-driven run before launching a long Agent experiment.
6. Publish a reviewed result copy for visitors after inspecting it.

Use [the architecture guide](architecture.md) for execution semantics and [the publication guide](public_deployment.md) for display coverage checks. Keep explanatory historical notes separate from the configuration actually resolved for a run.
