# Cobweb: delayed supply responses and market prices

[Guide index](../configuration.md#scenario-guide-index) · [Experiment conditions](../experiments.md) · [Results interface](../interface_and_artifacts.md)

## Economic meaning

Production takes time. A producer can choose today's output using yesterday's attractive price, then bring that output to a market whose price falls when supply expands. The resulting low price can induce a smaller next production decision, followed by a price recovery. Repeated delayed responses create the alternating price–quantity path known as the cobweb.

The classical model distinguishes contracting, persistent, and expanding oscillations according to the relative supply and demand response slopes. See [Ezekiel, *The Cobweb Theorem* (1938)](https://academic.oup.com/qje/article-abstract/52/2/255/1862362). Here those regimes provide scripted calibration cases, while enterprise decisions can supply the feedback quantity in Agent conditions.

## What the platform simulates

One **Manufacturer** produces `beer` for an external price-setting market. This scenario is grouped under **Multi-Enterprise Scenarios** because it uses the economic-scenario workflow; the configured cobweb producer itself is a single enterprise.

The reference equations use quantity per unit price:

```text
Demand:               Qd(t)    = a - b * P(t)
Lagged supply target:  Qref(t)  = c + d * P(t-1)
Market price:         P(t)     = clip((a - Qmarket(t)) / b, price bounds)
Reference equilibrium: P*      = (a - c) / (b + d)
                      Q*      = a - b * P*
```

Supply and demand quantities also have configured bounds. In the unbounded one-lag reference model, `d/b < 1`, `= 1`, or `> 1` identifies convergence, neutral cycling, or divergence. The implemented bounds limit excursions in the divergent case.

| Regime | a | b | c | d | d/b | Price seed |
| --- | --- | --- | --- | --- | --- | --- |
| Convergent | 160 | 0.50 | 20 | 0.25 | 0.50 | 280 |
| Neutral | 160 | 0.40 | 20 | 0.40 | 1.00 | 300 |
| Divergent | 170 | 0.25 | 20 | 0.60 | 2.40 | 280 |

Prices are in SCU per product unit. All three use a 40–600 SCU price interval; quantity ceilings are 240 for the convergent case and 260 for the other two.

### Decision feedback and cold start

C1 uses `scripted_formula`: the rule controller constructs the reference production response. C2 and C3 use `agent_endogenous`, with `production_plan_created` as the configured supply source and a one-round endogenous-supply lag. The environment reads the available plan-derived quantity, applies bounds, and records its source. When that source is unavailable, the configured fallback is `theoretical_lagged_supply`.

The initial price is the lagged-price seed, not necessarily the observed R0 clearing price. In the divergent cold start, `20 + 0.6 × 280 = 188` units of reference supply lead to the price floor of 40 and external demand of 160. Use `market_supply_source` and its detail record to distinguish this seed calculation from subsequent enterprise-plan feedback.

Market-feedback supply, created plan quantities, completed production, inventory, and customer fulfillment are different recorded quantities. Their joint inspection connects price dynamics to business feasibility.

## Experiment conditions

| Role | Exact scenario ID | Default duration |
| --- | --- | --- |
| Convergent scripted calibration | `cobweb_convergent_scripted` | 40 rounds |
| Neutral scripted calibration | `cobweb_neutral_scripted` | 40 rounds |
| Divergent scripted reference, C1 | `cobweb_divergent_scripted` | 40 rounds |
| Divergent mechanism-constrained Agent, C2 | `cobweb_divergent` | 40 rounds |
| Divergent operating-objective Agent, C3 | `cobweb_divergent_profit_objective` | 40 rounds |

The five runs above form the main calibration/comparison design. Main formal summaries use R3–R37. C2 emphasizes the mechanism-primary production response with enterprise guidance. C3 uses `profit_balanced` decisions and disables that mechanism guidance, allowing operating objectives to change the supply response.

Exploratory `cobweb_convergent`, `cobweb_neutral`, and `cobweb_convergent_profit_objective` entries are also registered, with 24-round defaults. They are separate from the five 40-round runs above. Cobweb has no dedicated SA preset in the current registry.

## Start a simulation in the interface

1. Enable and unlock an [operator deployment](../visitor_mode.md#enable-experiment-execution).
2. Open **Experiment Operations → Multi-Enterprise Scenarios → Cobweb Model → Configure Startup**.
3. In **Experiment / ablation condition**, select the exact scenario ID. Run the three scripted calibration entries first if you want to inspect the environment without model calls.
4. Check **Experiment Role**, **Parameter Regime**, **Response Controller**, **Decision Profile**, **Supply feedback**, and **Mechanism guidance**. Select a 40-round main entry for the formal design.
5. Keep the selected controller and defaults, then click **Run now**. Each condition creates an independent job and archive.
6. For Agent C2/C3, configure your own model service before launching. Follow **Task Status** / **Run Details** and select the completed archive through **Review Results**.

Visitors can inspect **View Configuration** and existing results but cannot launch. The configuration dialog's exact scenario ID resolves any variation in the preset's display name.

## Read the result

Open **Scenario Analysis → Cobweb Model**. Inspect the following sequence:

1. **Parameters and source coverage:** confirm the slope regime, feedback lag, actual market-supply sources, and fallback rounds.
2. **Price and supply paths:** compare observed values with the equilibrium and theoretical response.
3. **Phase-space path:** see whether successive price–quantity points contract, cycle, or expand toward bounds.
4. **Deviation summaries:** compare price/supply deviations and late-to-early deviation ratios over the same window.
5. **Operating state:** use Round View to inspect the plans, production receipts, inventory, sales, and liquidity underlying the supply response.

For C3, a smaller price deviation can coexist with a different output, inventory, or service trajectory. Explain the changed decisions and supply sources together with the economic indicators. The regime label describes the reference parameters; the recorded Agent path provides the behavioral result.

## Files and implementation

| Artifact relative to a run | Purpose |
| --- | --- |
| `run_meta.json` | Cobweb parameters, controller profile, formal role, and duration |
| `public/exchange/dayN/external_demand.json` | Cobweb market history, prices, reference/feedback supply, and source details |
| `enterprises/Manufacturer/department/production/dayN/production_action.json` | Production decisions |
| `enterprises/Manufacturer/department/production/dayN/production.json` | Recorded production state |
| `enterprises/Manufacturer/department/inventory/dayN/inventory.json` | Inventory available to operations |
| `enterprises/Manufacturer/department/sales/dayN/sales.json` | Orders and fulfillment |

The market equations and source selection are in [market_manager.py](../../core/market_manager.py); parameter construction and formal roles are in [simulation_preset_config.py](../../config/simulation_preset_config.py). [CobwebModelValidationView.jsx](../../visualization/src/components/CobwebModelValidationView.jsx) provides the calibration and operating views. Use the [common export/publication workflow](../public_deployment.md) to distribute results.
