"""Existing action semantics for isolated architecture experiments, not a policy.

This read-only contract explains execution side effects. It neither selects nor
reorders actions, changes economic parameters, nor turns refusals into successes.
"""

ACTION_CONTRACT_VERSION = "firm_action_execution.v1"


def action_execution_contract(departments):
    rules = {
        "sales": {
            "adjust_sales_demand": [
                'quantity must be strictly positive; zero does not pause, cancel or clear supply.',
                'A new sales request for the same enterprise/product replaces old open/reserved requests and supersedes associated pending proposals.',
                'Do not update a request and then respond to its invalidated proposals. To retain an accepted proposal, do not replace its request before exchange confirmation; reversing the two actions may still invalidate it because the proposal remains pending until responses are collected.',
                'Use explicit action_pass when adding no supply. Pass neither cancels existing requests nor rejects proposals. The action set has no sales-request cancellation action; do not invent one.',
            ],
            "proposal_response": [
                'Respond only to actionable proposals to which this enterprise has not responded. Do not republish a sales request merely to accept an old proposal.',
            ],
        },
        "procurement": {
            "request_lifecycle": [
                'A new purchase request replaces old open/reserved requests for the same enterprise/material and supersedes their pending proposals.',
                'create_purchase_demand and create_replenishment_order that actually creates a request may replace existing requests. Do not replace a request supporting an accepted, unconfirmed proposal; accepting before updating may still invalidate it.',
            ],
            "create_purchase_order": [
                'Each direct external purchase occupies procurement workers until arrival or legal cancellation. Execute orders sequentially within an action array; earlier orders reduce staffing, cash and credit available to later orders.',
                'Check available/allocated under coordination.departments.hr.department_staffing.PROCUREMENT and assigned_workers on unreceived orders. Total headcount is not available headcount.',
                'Multiple materials require multiple orders. Reducing quantities does not reduce minimum staff per order or allow reusing one available worker across three simultaneous orders. Apply staffing relaxation only if the current regime enables it.',
            ],
            "create_replenishment_order": [
                'The replenishment algorithm computes request quantities from local state and legal parameters. quantity is not an override for the generated request amount.',
                'A zero suggested_order_quantity can return success with created_request=false. That is no request, not a successful order or future arrival; inspect requested_quantity/request_id.',
            ],
        },
    }
    return {
        "schema_version": ACTION_CONTRACT_VERSION,
        "scope": 'Legality, order and side effects of existing actions only; this does not authorize new actions or prescribe purchase quantities, inventory targets or economic-effect direction.',
        "common": [
            'Route functions in their existing layout order and execute each submitted array in order. Do not reorder economic decisions for the model; account for earlier actions changing later resource availability.',
            'Current policy_context/action_constraints determine action availability. This contract expands no permissions. Use only authorized facts and templates.',
            'Proposal acceptance, order creation, fulfillment and physical arrival are distinct events. Acceptance is not delivery.',
            'Preserve real rejection receipts. With attempts remaining, read current feedback and repair rejected actions only; never replay successful actions or actions with unknown receipts.',
        ],
        "departments": {dept: rules[dept] for dept in departments if dept in rules},
    }
