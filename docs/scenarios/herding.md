# Herding: peer information and synchronized production

[Guide index](../configuration.md#scenario-guide-index) · [Experiment conditions](../experiments.md) · [Results interface](../interface_and_artifacts.md)

## Economic meaning

Firms can treat other firms' actions as information about market opportunities. When many decision makers follow similar signals or imitate one another, production or investment decisions may become synchronized. If collective expansion exceeds demand, the resulting inventory and cash pressure can persist after enthusiasm falls.

[Informational-cascade theory](https://www.journals.uchicago.edu/doi/10.1086/261849) explains one route from observing others to conformity. The platform examines a related enterprise setting: production decisions under common market signals, with or without access to aggregate peer information. Synchronization alone can also arise from the common signal, so the visible/hidden comparison is central to interpreting it.

## What the platform simulates

Four producers, **Sensor_A–Sensor_D**, sell `smart_sensor` to an external market. Their operating observations include their own business state and market signals. A visibility switch controls access to an environment-produced peer summary; it does not grant direct access to another firm's private files.

| Main preset setting | Value |
| --- | --- |
| Duration | 40 rounds, R0–R39 |
| Formal evaluation window | R3–R37 |
| Aggregate true demand | Rises from 80 to 150, then falls to 60 and remains there |
| Market heat | Rises from 0.35 to 0.95, then falls to 0.30 |
| Peer-summary lag | 1 round |
| Production-feedback lag | 1 round |
| Cold-start reference plan | 70 units per enterprise |
| Base unit price / reference unit cost | 420 / 210 SCU |

The market records the source of supply estimates, including cold-start reference quantities before a usable enterprise plan exists. Separate these source flags from later plan-driven behavior when tracing an expansion.

## Experiment design

| Condition | Peer visible | Peer hidden |
| --- | --- | --- |
| C1: fixed-rule reference | `herding_baseline_peer_visible_scripted` | `herding_no_peer_visibility_scripted` |
| C2: mechanism-constrained Agent | `herding_baseline_peer_visible` | `herding_no_peer_visibility` |
| C3: operating-objective Agent | `herding_peer_visible_profit_objective` | `herding_no_peer_visibility_profit_objective` |
| SA: unified enterprise decision under frozen C3 settings | `firm_ablation_herding_visible_sa` | `firm_ablation_herding_hidden_sa` |

The visible/hidden pair tests the information interface within a chosen controller regime. C2/C3 compare mechanism-oriented and operating-objective behavior. C3/SA compare enterprise decision organization under corresponding visibility settings. These are distinct comparisons: compare visible C3 with visible SA, and hidden C3 with hidden SA.

SA leaves the four firms separate; each firm consolidates diagnosis and its enabled functional decisions. Optional economic `*_ma` entries are legacy protocols, not substitutes for the C3 references. See [economic architecture comparisons](../experiments.md#economic-architecture-comparisons).

## Start a simulation in the interface

1. Enable and unlock an [operator deployment](../visitor_mode.md#enable-experiment-execution). Scripted C1 needs no model credentials; C2, C3, and SA need your configured model service.
2. Open **Experiment Operations → Multi-Enterprise Scenarios → Herding Effect → Configure Startup**.
3. Select the exact ID in **Experiment / ablation condition**. Choose both the controller regime and the visible/hidden variant deliberately.
4. Check the duration, `smart_sensor` demand product, four external-order targets, and peer-visibility settings in the configuration. Use 40 rounds for the main comparisons.
5. Keep defaults or enable **Modify Configuration** for an exploratory run, then click **Run now**.
6. Run the corresponding second condition as a separate job. Record its run ID rather than overwriting the first job.

Visitors can open **View Configuration** and browse existing results; execution requires operator access. Select a completed run through **Review Results**, then open **Scenario Analysis → Herding Effect**.

## Read the result

Begin with the true-demand/market-heat timeline, then compare enterprise plans and plan-source coverage. Follow synchronized expansion into realized output, sales, inventory, and cash rather than treating the plan itself as delivered production.

| Metric | Implemented meaning |
| --- | --- |
| Synchronization index | `clip(1 - CV(planned quantities), 0, 1)` across enterprises; zero for a non-positive mean or fewer than two observations |
| Overproduction ratio | Aggregate planned quantity divided by true demand |
| Inventory pressure | Aggregate inventory divided by true demand |
| Growth synchronization | Fraction of comparable enterprise plan changes aligned with the heat trend |
| Herding index | Weighted combination: 0.45 synchronization + 0.35 clipped heat + 0.20 clipped excess-production component |
| Cash stress | Fraction of recorded enterprises with negative cash |

The herding index is an implemented descriptive indicator. Read its components and the visible/hidden difference to understand which behavior contributes to it. A larger index can reflect strong common heat as well as convergence of firm plans.

Useful inspection steps are:

1. Locate the heat peak and cooling period.
2. Check whether individual firms expand together and how quickly they contract afterward.
3. Compare overproduction and inventory pressure in the matching hidden run.
4. Inspect production actions, receipts, and financial states for the firms driving the difference.
5. For C3/SA, add task validity, retries, repair outcomes, and duration from the evidence panel. Keep these execution measures separate from the economic synchronization measures.

The heatmap and SVG charts support **Enlarge chart**, **Close chart**, and Escape. **Round View** provides firm-level records; **Operations Review** helps trace operating quantities over time.

## Files and implementation

| Artifact relative to a run | Purpose |
| --- | --- |
| `run_meta.json` | Controller, visibility, lag settings, and full configuration |
| `public/exchange/dayN/herding_metrics.json` | Signals, plan sources, firm plans, synchronization, and overproduction |
| `public/exchange/dayN/end_of_day/herding_metrics.json` | End-of-day version, when present; preferred by the viewer |
| `public/exchange/dayN/external_demand.json` | External demand and market history |
| `enterprises/Sensor_A/department/production/dayN/` | Production actions, receipts, and state; analogous paths exist for other firms |

The mechanism is implemented in [market_manager.py](../../core/market_manager.py), with information policy and presets in [simulation_preset_config.py](../../config/simulation_preset_config.py). [HerdingEffectView.jsx](../../visualization/src/components/HerdingEffectView.jsx) supplies the signal, plan, pressure, and synchronization views. [Publication tools](../public_deployment.md) prepare result copies for visitors.
