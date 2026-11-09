# Bullwhip: demand propagation through a supply chain

[Guide index](../configuration.md#scenario-guide-index) · [Experiment conditions](../experiments.md) · [Results interface](../interface_and_artifacts.md)

## Economic meaning

A retailer observes customer demand, while upstream firms mostly observe orders placed by their immediate customers. Replenishment decisions therefore become signals for the next tier. Forecast updates, batching, delays, and inventory recovery can turn a modest demand change into larger upstream order fluctuations. This is the bullwhip effect: ordering variability can exceed the variability of final demand.

The economic question is how local purchasing and service decisions produce system-wide inventory, backlog, and delivery consequences. The classical account is [Lee, Padmanabhan, and Whang, *Information Distortion in a Supply Chain: The Bullwhip Effect* (1997)](https://pubsonline.informs.org/doi/10.1287/mnsc.43.4.546). This implementation exposes requests, negotiated commitments, and deliveries separately so that their propagation can be examined.

## What the platform simulates

Goods move along **Supplier → Manufacturer → Distributor → Retailer → external customers**. Purchase requests move in the opposite direction. The Supplier acquires external inputs, the Manufacturer converts materials into beer, and the downstream firms distribute it. Firms manage cash, inventory, staffing, procurement, sales, and, where enabled, production.

A purchase request can receive a proposal, gain bilateral acceptance, become a confirmed order, and eventually be delivered or breached. A large request is therefore different from a large shipment. The environment executes the business lifecycle; the controller selects actions within it.

| Default setting | Value |
| --- | --- |
| Main C1/C2/C3 duration | 40 rounds, R0–R39 |
| Formal evaluation window | R3–R37 |
| External demand | 40 units in R0–R3; 80 units from R4 onward |
| Customer delivery lead time | 1 round |
| External customer unit price | 280 SCU |
| Firms | Supplier, Manufacturer, Distributor, Retailer |

These values describe the named main presets. Job configuration snapshots record any launch-time changes.

## Experiment conditions

| Purpose | Exact scenario ID | Controller |
| --- | --- | --- |
| C1: rule-based mechanism reference | `beer_game_scripted` | Fixed Rule Script |
| C2: constrained mechanism reproduction | `beer_game` | Agent Skills |
| C3: operating-objective behavior | `beer_game_profit_objective` | Agent Skills |
| SA: enterprise-organization ablation under a frozen C3 regime | `firm_ablation_bullwhip_sa` | Unified enterprise Agent |

C2 enables scenario-specific pass-through, proposal-conversion, upstream-amplification, and supplier pull-through guidance. C3 disables those four bullwhip guidance policies and uses profit, cash safety, service, and risk objectives. Thus C2 asks whether the configured mechanism can be reproduced; C3 examines how operating decisions reshape propagation.

SA keeps multiple enterprises and the economic environment, but consolidates each enterprise's diagnosis and functional decisions into one LLM task. Its intended organizational reference is C3. The optional `firm_ablation_bullwhip_ma` entry is a legacy protocol with different settings; use the [comparison rules](../experiments.md#economic-architecture-comparisons) when selecting a control.

Other beer-game, distribution, credit, and historical baseline entries are exploratory presets. Identify a run by its exact scenario ID and frozen configuration rather than by the shared Bullwhip Effect card alone.

## Start a simulation in the interface

1. Follow [operator setup](../visitor_mode.md#enable-experiment-execution) and open the interface at `http://127.0.0.1:3000`.
2. Open **Browse Experiment Designs** / **Experiment Operations**, enter the operator access token, and select **Unlock controls**.
3. Select **Multi-Enterprise Scenarios**, locate **Bullwhip Effect**, and click **Configure Startup**.
4. In **Experiment / ablation condition**, select the exact scenario ID from the table. For a first engine check, select `beer_game_scripted`; it needs no model service.
5. Review the experiment group, 40-round duration, primary window, external-order product, and target enterprise. Keep preset defaults for a reference run. **Modify Configuration** enables temporary edits where the selected condition permits them.
6. Click **Run now**. Follow the job in **Task Status**, **Run Details**, and the job history. After completion, use **Review Results** and select its run ID.

A visitor sees **View Configuration** and can inspect results, but launch and condition-edit controls are locked. Enable an operator deployment to run a new simulation. Automatic multi-condition batches in the current interface apply to FD cases; run economic conditions as separate jobs.

## Read the result

Open **Scenario Analysis → Bullwhip Effect**. Use **Effective Propagation** for currently effective purchase requests and **Raw Requests** for all recorded request intent, including requests later superseded or expired. Absolute, log-scale, and relative-peak displays answer different magnitude questions.

| Question | Evidence to inspect |
| --- | --- |
| Where is demand amplified or attenuated? | Tier request curves, variance/CV amplification, cumulative transmission ratios, and the heatmap |
| Do requests become commitments? | Requests, proposals, acceptances, and confirmed-order quantities |
| Do commitments become goods? | Procurement arrivals, sales fulfillment, backlog, and order status |
| What happens inside each firm? | Round View: decisions, receipts, cash, inventory, and production |
| What changes in the network? | Propagation Review: selected-round flows; Global Review: system trajectories |

Variance amplification compares the variance of the selected upstream quantity series with the selected demand or downstream series. CV-based amplification additionally normalizes variability by the series mean. Compare the same request definition, quantity units, denominator, and round window across runs. The chart's static summaries and a paper's R3–R37 calculation need not cover the same rounds: export the specified window for that analysis. If using an observed-prefix calculation, report it alongside the fixed-window calculation rather than substituting one for the other.

**Finished-Goods Equivalent** converts compatible inputs using recipes. External supplier/customer switches add those boundaries to applicable views. An empty current-round flow chart means no flow was recorded in the selected view; it does not erase cumulative earlier trades.

For an architecture comparison, also inspect valid-output counts, task and business retries, executed replacements, timeouts, unresolved failures, and whole-run duration. These reveal how the organization operates even when economic outcomes do not rank uniformly.

## Files and implementation

Paths below are relative to a run directory:

| Artifact | Contents |
| --- | --- |
| `run_meta.json` | Scenario, resolved configuration, controller identity, timing, and completion |
| `public/exchange/dayN/bullwhip_metrics.json` | Raw/effective series and recorded amplification summaries |
| `public/exchange/dayN/exchange.json` | Requests, proposals, and order lifecycles |
| `public/exchange/dayN/external_demand.json` | Terminal customer demand |
| `enterprises/FIRM/department/procurement/dayN/procurement.json` | Procurement and arrivals |
| `enterprises/FIRM/department/sales/dayN/sales.json` | Sales, commitments, and fulfillment |

Presets are in [simulation_preset_config.py](../../config/simulation_preset_config.py). Propagation metrics are assembled in [controller.py](../../core/controller.py); the transaction lifecycle is in [exchange_manager.py](../../network/exchange_manager.py). The [bullwhip view](../../visualization/src/components/BullwhipEffectView.jsx) identifies its input series. Use [publication tools](../public_deployment.md) to share a reviewed copy of a completed run.
