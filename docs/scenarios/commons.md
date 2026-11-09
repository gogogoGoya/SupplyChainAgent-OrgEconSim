# Tragedy of the commons: extraction and shared-resource pressure

[Guide index](../configuration.md#scenario-guide-index) · [Experiment conditions](../experiments.md) · [Results interface](../interface_and_artifacts.md)

## Economic meaning

A shared resource creates an interdependence between individual incentives and collective outcomes. A firm receives the benefit of its own extraction, while depletion and quality loss reduce the resource available to all participants. Aggregate extraction can therefore exceed renewal even when each firm is pursuing an individually attractive opportunity.

This incentive conflict motivates the [tragedy-of-the-commons account](https://doi.org/10.1126/science.162.3859.1243). The experiment examines whether operating policies produce competitive expansion, resource degradation, and subsequent business pressure. It varies resource resilience and firm behavior rather than assuming that every shared-resource setting must collapse.

## What the platform simulates

Four firms, **Miner_A–Miner_D**, acquire `copper_ore` from `shared_copper_deposit` and serve an external market. The stock has configured renewal, extraction costs, and stock-dependent quality. Copper is the product label for this renewable-pool experiment; the replenishment rule is an economic simulation mechanism.

The environment records planned extraction, realized acquisition, resource quality, remaining stock, and per-firm operating state. In stress presets, effective acquisition constrains production and planned acquisition determines external order demand. This exposes the consequences of committing to more output than the degraded resource can support.

| Preset family | Initial stock | Renewal per round | Quality floor | Economic coupling |
| --- | --- | --- | --- | --- |
| Basic baseline | 12,000 | 360 | 0.35 | Acquisition costs are not charged through the dedicated finance switch; effective acquisition is not a production cap |
| Uniform/differentiated stress | 7,000 | 100 | 0.15 | Acquisition costs charged; production constrained by effective acquisition; orders based on planned acquisition |
| Financial-pressure stress | 6,500 | 80 | 0.08 | Same coupling switches, with additional price/cost pressure |

These presets use fixed renewal. A quoted sustainability target is a reference for aggregate behavior, not an enforced per-firm quota. The registered experiments below model ungoverned competition: governance is disabled, and quota/tax fields are inactive.

## Experiment design

| Purpose | Exact scenario IDs |
| --- | --- |
| Basic mechanism setup | `commons_mining_baseline_scripted`, `commons_mining_baseline` |
| Uniform high-growth stress | `commons_mining_collapse_baseline_uniform_scripted`, `commons_mining_collapse_baseline_uniform` |
| Heterogeneous-strategy stress | `commons_mining_collapse_baseline_differentiated_scripted`, `commons_mining_collapse_baseline_differentiated` |
| Financial-pressure stress | `commons_mining_financial_damage_baseline_scripted`, `commons_mining_financial_damage_baseline` |
| C3 operating-objective condition | `commons_mining_profit_objective` |

The `_scripted` entries are C1 references; their paired unsuffixed entries are C2 Agent conditions. Uniform stress gives firms a shared high-growth competitive profile. Differentiated stress assigns conservative, balanced, high-growth, and opportunity-following profiles. C3 uses the differentiated stress resource regime and operating objectives that can include longer-run resource risk.

All entries above default to 40 rounds; the main formal window is R3–R37. `commons_mining_collapse_baseline` is an additional alias of the differentiated stress setup. There is no dedicated commons SA preset in the current registry.

A comparison should retain the same resource/financial regime: changing from the basic pool to the fragile pool changes the environment as well as its possible outcomes. To study operating objectives, compare the relevant C2 stress condition with C3; to study strategy heterogeneity, compare uniform with differentiated stress while retaining their resource settings.

## Start a simulation in the interface

1. Enable and unlock an [operator deployment](../visitor_mode.md#enable-experiment-execution).
2. Open **Experiment Operations → Multi-Enterprise Scenarios → Tragedy of the Commons → Configure Startup**.
3. Choose the exact ID in **Experiment / ablation condition**. For a first model-free check, select `commons_mining_collapse_baseline_differentiated_scripted`.
4. Inspect the stock, renewal, quality, acquisition-cost, production-coupling, and governance settings. Keep 40 rounds for the formal conditions.
5. Use **Modify Configuration** only for an intended exploratory variation. Click **Run now**, then follow **Task Status** and **Run Details**.
6. Run the paired condition separately, keeping its run ID. C2/C3 require your configured model service; C1 does not.

Visitors can inspect **View Configuration** and archived results. After completion, choose **Review Results → Scenario Analysis → Tragedy of the Commons**.

## Read the result

Follow the sequence **extraction decisions → aggregate pressure → stock/quality → effective supply → operating outcomes**.

| Question | Recorded evidence |
| --- | --- |
| Is extraction above the pool's renewal capacity? | Planned and realized acquisition, renewal, and sustainability reference |
| Is pressure degrading the shared stock? | Remaining stock, stock ratio, quality, and warning/collapse indicators |
| Do firms respond differently? | Per-firm acquisition trajectories and strategy profiles |
| Does degradation affect execution? | Effective acquisition, constrained production plans, output, and order fulfillment |
| Does it affect finances? | Acquisition costs, revenue, cash, and profit in the corresponding financial regime |

The resource panel shows stock, quality, collective extraction, and firm trajectories. Use its final-stock and cash displays alongside the action/receipt evidence in **Round View** and quantities in **Operations Review**.

Threshold labels are configured indicators. A threshold crossing documents the modeled stock condition; financial damage requires corresponding finance records. In particular, the basic preset's finance and production-coupling switches differ from the stress presets, so their resource and profit curves answer different questions.

## Files and implementation

| Artifact relative to a run | Purpose |
| --- | --- |
| `run_meta.json` | Resource regime, firm strategy profiles, governance, and finance-coupling switches |
| `public/exchange/dayN/shared_resource_metrics.json` | Acquisition, renewal, remaining stock, quality, and enterprise records |
| `public/exchange/dayN/end_of_day/shared_resource_metrics.json` | End-of-day metrics, when present; preferred by the viewer |
| `public/observer_state/dayN/end_of_day/Miner_A.json` | Final operating state for one firm; analogous files exist for all miners |
| `enterprises/Miner_A/department/production/dayN/` | Plans, execution feedback, and production state |
| `enterprises/Miner_A/department/finance/dayN/finance.json` | Recorded cash, revenue, and costs |

The pool update and business coupling are implemented in [market_manager.py](../../core/market_manager.py); presets and strategy assignments are in [simulation_preset_config.py](../../config/simulation_preset_config.py). [CommonsTragedyView.jsx](../../visualization/src/components/CommonsTragedyView.jsx) supplies the resource and operating views. See [result publication](../public_deployment.md) for sharing a reviewed archive.
