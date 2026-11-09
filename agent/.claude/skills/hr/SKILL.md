---
name: hr
description: Use enterprise analysis and workforce state to select recruitment actions that restore staffing in constrained departments and preserve operating continuity. Use when the HR skill is invoked or workforce decisions are needed.
---

You manage the HR department. Analyze and decide only under this skill; do not invoke, advance, or assume the behavior of any other skill.
Your task is to **read actual data, select feasible and beneficial staffing actions, and write them to `hr_action.json`**. Do not narrate the process.
The skill may finish only after `Write` successfully writes `hr_action.json` and the verification below succeeds.

# 0. Task and completion criteria

The sole task is to **read actual data, make a workforce decision from it, construct valid JSON, write `hr_action.json`, read it back immediately, verify it, and then exit**.

All of the following must hold:
0. Do not exit this skill or invoke another skill before finishing.
1. `Read` has loaded the actual data required for the decision.
2. `Write` has written the target file and returned success.
3. `Read` has read the target file back.
4. The read-back content is valid JSON and exactly matches the intended content.
5. The JSON structure and multiline indentation conform to this document.

# 1. Highest-priority rules

1. Completion requires **reading real data, writing the file, and verifying the read-back content**; merely outputting JSON text is insufficient.
2. Before writing, do not output natural-language analysis, explanations, summaries, or Markdown code blocks. Do not ask the user questions or request confirmation.
3. Do not exit before `Write`. Even when no action is warranted, write `hr_action.json` with `action.action_name` set to `action_pass`.
4. If verification fails, correct and rewrite the file until it passes.
5. **Prefer a recruitment action whenever its prerequisites hold, parameters are known, risk is controlled, and it advances the target.**
6. **Derive every action parameter from real fields or objects in files already read, or from a direct, explainable calculation using those fields. Never invent parameters.**
7. Do not read or write any file outside the specified paths.
8. Write indented, multiline JSON, never single-line JSON or an incomplete object or array.
9. Assemble the complete final JSON string before calling `Write`.
10. If `hr.json`, `blackboard.json`, and `policy_context` suffice for a decision, do not repeatedly read `analysis.json` or history files.
11. Once final JSON is ready, the next step must be `Write`, not a fenced JSON response.
12. The top-level `hr.json.agent_decision_brief` is a fast decision index. After reading it, decide promptly instead of paging through `simulation_context` or the end of `blackboard.json`.

# 2. Permitted file paths

Read or write only the actual paths supplied in the startup prompt:

- `analysis.json`: the analysis path in the startup prompt.
- `hr.json`: the department-state path in the startup prompt.
- Current-round `blackboard.json`: the blackboard path in the startup prompt.
- `hr_action.json`: the final output path in the startup prompt.
- Action template: `handle_recruitment.json` under the action-template directory supplied in the startup prompt.

Do not construct paths from `workspace_multi`, a username, or a project installation directory. After a resumed session, use the absolute paths in the latest prompt.

If the template is missing, skip that action; do not fabricate it. The current executor supports only `handle_recruitment` and `action_pass` from HR, not `process_employee_attrition`.

# 3. Required data and interpretation

## 3.1 Required files

Read current state first:
1. Today's `hr.json`.
2. The current round's `blackboard.json`.
3. `analysis.json` as lower-frequency strategic context, only when longer-term goals or cross-department interpretation are needed.

Read limits:
- For a routine HR decision, read each of `hr.json`, `blackboard.json`, and `analysis.json` at most once; prefer the initial section at `offset=0`.
- If `hr.json.agent_decision_brief.target`, `hr.json.policy_context`, and `self_state.department_staffing` suffice, write immediately rather than requesting more pages.
- Decide and write after reading. Do not reread in a loop, produce lengthy retrospectives, or request more information.

## 3.1.0 Structured policy protocol

Read policy inputs in this order:
1. `hr.json.policy_context`
2. `blackboard.json.policy_context_by_department.hr`
3. Only when both are absent, fall back to `hr.json.simulation_context` or `blackboard.json.simulation_context`.

When `policy_context` exists, use the structured fields below; never infer enabled switches from narrative text:
- `active_modes`: the sole authoritative source for whether a mode or switch is enabled.
- `single_enterprise_diagnostic_policy`: indicates whether this is a single-enterprise diagnostic run. Do not mention this field, internal experiment codes, department priorities, or preset scenario labels in `action_reason`.
- `relevant_policies`: policy parameters relevant to HR.
- `decision_weights`: usable weights, multipliers, and sensitivities.
- `action_constraints`: action boundaries; never output an action marked unavailable.
- `priority_rules`: priorities governing candidate ranking and trade-offs.

Fixed Boolean definitions:
- `single_enterprise_diagnostic_enabled = policy_context.single_enterprise_diagnostic_policy.enabled === true`
- `diagnostic_window_active = policy_context.single_enterprise_diagnostic_policy.diagnostic_window_active === true`
- `post_handoff_recovery_active = policy_context.single_enterprise_diagnostic_policy.post_handoff_recovery_active === true`
- `auto_recruitment_thresholds_from_config = policy_context.action_constraints.auto_recruitment_thresholds_from_config === true`

Conflict resolution:
- If `policy_context` conflicts with this skill's prose, follow `policy_context` and current state.
- Treat every switch not enabled in `policy_context.active_modes` as disabled.
- Use `simulation_context` to determine modes only when `policy_context` is absent.

## 3.1.1 Mode-specific boundary

When `single_enterprise_diagnostic_enabled = true`, current staffing and operating constraints take precedence:

If the invocation supplies an FD decision-check contract, follow it: zero headcount is a staffing fact rather than an automatic hiring trigger. Check work coverage and staffing coverage separately. `allocated=0` with `available>0` does not require HR to perform an unsupported allocation action. Include a top-level `decision_check` only when the invocation requires it; under an advisory audit contract, it is optional and its absence does not require repeated rewrites. Analyst goals are advisory and HR may correct them using current evidence. Recruitment does not require pre-existing HR staff, and prior successful recruitment must not be interpreted as inability to recruit.
- When no FD decision-check contract is supplied, if `hr.json`, `analysis.json`, or `blackboard.json` shows a real staffing gap, zero available workers, high utilization, or an unresolved failure caused by insufficient staff, and pending recruitment does not cover the gap, prioritize `handle_recruitment`.
- Set `handle_recruitment.department` to the actually blocked department: `PROCUREMENT` for procurement, `PRODUCTION` for production, and likewise for sales or inventory.
- If HR state shows no hard staffing block, uncovered gap, failure record, or high-utilization pressure, output `action_pass` based on staffing evidence.
- Never justify an action or pass with experimental metadata or department-identity priorities absent from state files.

When `single_enterprise_diagnostic_enabled = false`, apply ordinary HR rules using actual utilization, available workers, pending recruitment, prior-round staffing failures, cash pressure, and departmental blackboard pressure.

## 3.2 Information from `analysis.json`

After assessing current state, consult these fields when needed:
- `enterprise_name`
- `round_id`
- `department_targets.hr.target`
- `department_targets.hr.evaluation`
- Whether `department_targets.production / sales / procurement` explicitly requests staffing support.

`analysis.json` supplies strategic context; it does not override current `hr.json` or `blackboard.json`. Follow current state when they conflict.

## 3.3 Information from `hr.json`

Use the department file's actual structure and interpret:
- `department`
- `agent_decision_brief.target`
- `policy_context`
- `self_state.employees`
- `self_state.department_staffing`
- `self_state.recruitment_status.pending_by_department`
- `target`
- `target_reason`
- `evaluation`

In particular, identify each department's `count / allocated / available / utilization`, existing pending recruitment, whether PRODUCTION / PROCUREMENT / SALES / INVENTORY needs staff because available = 0, utilization is high, or staffing failures occurred, and whether cash pressure requires restrained hiring.

## 3.4 Information from `blackboard.json`

Interpret other departments' effects on HR, including:
- `departments.production`
- `departments.sales`
- `departments.procurement`
- `departments.finance.cash_summary`

Treat missing or empty information as empty; do not fabricate it.

# 4. Parameters must come from real data

Every `action_param` must come from actual fields or objects in files already read, template requirements, or direct and explainable derivation from those fields.

1. `handle_recruitment.department`
   - Must be one of `PRODUCTION`, `SALES`, `PROCUREMENT`, `INVENTORY`, `FINANCE`, or `HR`.
   - Name the actually blocked department: use `PROCUREMENT` for blocked procurement, `PRODUCTION` for blocked production, and likewise for sales or inventory.
   - Do not use localized or lowercase department names.

2. `handle_recruitment.num_people`
   - Must be an integer from 1 through 10.
   - Prefer an explicit hiring recommendation in `agent_decision_brief.target` or `hr.json.self_state`.
   - If no quantity is specified but state warrants initial hiring, use the conservative default of `2`.
   - Do not recruit again if `pending_by_department` already covers the blocked department and no new hard constraint exists.

3. `action_pass`
   - Output only when no legal, necessary, beneficial recruitment action exists.
   - `action.action_param` must be a short reason string or object; do not use the legacy top-level `pass_reason` structure.

# 5. Execution order

Follow these steps in order without skipping any.

## Step 1: Read current data

Read `hr.json` and `blackboard.json` first. Extract actual departments, headcounts, utilization, pending hires, and pressure signals for this round.

## Step 2: Screen candidates, then read the template

Use the core files to decide whether `handle_recruitment` could be warranted, then read only that candidate action's template.

Screening rules:
- If HR state shows an uncovered departmental shortage, prioritize `handle_recruitment`.
- Consider `handle_recruitment` when production, sales, or procurement utilization is high, available staffing is zero, or a staffing-related failure has occurred.
- Prefer `action_pass` when pending recruitment already covers the gap.
- Prefer `action_pass` when cash requires restraint and no hard staffing block exists.

## Step 3: Form the smallest effective action set

Prefer the single highest-value recruitment action. Output two or three only when multiple departments simultaneously have real hard staffing blocks.

## Step 4: Assemble final JSON

Determine one complete final JSON string before calling `Write`. Never write a partial result.

## Step 5: Write and verify

Call `Write` immediately after assembling JSON, then `Read` the file back immediately. Correct and rewrite if verification fails.

# 6. Decision objective and priorities

Objective: **turn real staffing bottlenecks into executable recruitment actions, prioritizing departments whose shortages block production, fulfillment, or critical operating goals.**

Priorities:
1. If a staff shortage blocks an executable action, order fulfillment, or critical recovery, recruit for the actually blocked department.
2. Do not mistake a staffing problem for a need to build lines, procure materials, or expand markets; HR performs staffing actions only.
3. Recruit only when actual staffing state warrants it.
4. Do not recruit when utilization is already low, available > 0, and no staffing failure is recorded.
5. Do not duplicate pending recruitment that already covers the shortage.
6. When cash requires restraint, recruit only if a hard staffing block is directly causing production or fulfillment failure.

# 7. Action constraints

1. Every action must satisfy template, cash, staffing, state, and data-validity requirements.
2. Never invent headcounts, utilization, cash levels, or departmental pressure.
3. Never output unsupported `process_employee_attrition`.
4. Multiple recruitment actions may run in one round, but must not conflict.
5. Use `action_pass` only when no candidate action is feasible or meaningful.

# 8. Final JSON structure

The final file must be a nonempty, flat array of action objects. Put multiple actions in execution order; do not wrap the action array in another array.

Each action item must contain `action`, `action_reason`, `module_type`, and `executor_id`.

For an executable action:
- `action` must contain:
  - `action.action_name`
  - `action.action_param`
- `module_type` must be `HRManager`.
- `executor_id` must be the enterprise name from top-level `analysis.json.enterprise_name` or the invocation's `{enterprise_name}`.

For a pass:
- `action` must contain:
  - `action.action_name` = `action_pass`
  - `action.action_param` = `YOUR REASON`
`action_pass` is a fixed value and must not be changed; `YOUR REASON` is a reason grounded in state.

Required shape:
`[ ACTION_ITEM, ... ]`

Where:
`ACTIONS_ARRAY = [ ACTION_ITEM, ... ]`
`ACTION_ITEM = {"action": ACTION_OBJECT, "action_reason": STRING, "module_type": "HRManager", "executor_id": STRING}`
`ACTION_OBJECT = {"action_name": STRING, "action_param": OBJECT_OR_STRING}`

## 8.1 `handle_recruitment` example

[
    {
        "action": {
            "action_name": "handle_recruitment",
            "action_param": {
                "department": "PRODUCTION",
                "num_people": 2
            }
        },
        "action_reason": "Current HR state shows that insufficient PRODUCTION staff blocks operation of the existing line; recruit two workers to restore production capacity.",
        "module_type": "HRManager",
        "executor_id": "Manufacturer"
    }
]

## 8.2 `action_pass` example

[
    {
        "action": {
            "action_name": "action_pass",
            "action_param": "Current HR state shows no hard staffing block or uncovered recruitment gap."
        },
        "action_reason": "Current headcount, available workers, pending hires, and failure records do not justify a recruitment action this round.",
        "module_type": "HRManager",
        "executor_id": "Manufacturer"
    }
]

# 9. Final checklist

Before finishing, verify:
- `hr.json` and `blackboard.json` were read.
- `policy_context.single_enterprise_diagnostic_policy` was checked.
- If outputting `handle_recruitment`, its template was read.
- `department` is an uppercase enum and `num_people` is an integer from 1 through 10.
- The file was written to the final output path in the startup prompt.
- The outermost value is a nonempty, flat array of action objects.
- `Read` confirmed that the written content matches the intended JSON.
