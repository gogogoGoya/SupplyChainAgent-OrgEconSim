# Enterprise agents, Skills, and feedback

[Architecture](architecture.md) · [Experiment conditions](experiments.md) · [Model setup](../README.md#agent-setup)

## Organizational enterprise decisions

The full organizational controller combines an enterprise Analyst with functional department Skills and private coordination. The Claude Agent SDK invokes the configured role-specific Skills. A department agent is a scoped decision role and Skill invocation; it does not require a different model provider or a permanent independent process for each department.

[EnterpriseRuntime.py](../agent/EnterpriseRuntime.py) manages firm working paths and context. [MultiEnterpriseAgentManager.py](../agent/MultiEnterpriseAgentManager.py) coordinates multiple firms. [The single-enterprise orchestrator](../runtime/single_enterprise/orchestrator.py) reuses this machinery for controlled FD cases.

| Role | Main decision responsibility | Typical business evidence |
| --- | --- | --- |
| Analyst | Diagnose operating constraints and produce enterprise/department objectives | Structured analysis, goals, and supporting facts |
| Buyer / procurement | Request inter-firm supply or select external replenishment | Requests, proposals, purchase orders, logistics, and arrivals |
| Seller / sales | Respond to counterparties, develop markets, and accept or reject orders | Proposals, commitments, market staffing, and fulfillment |
| Production | Plan output and manage production-line capacity | Plans, line construction, interruptions, and completed output |
| HR | Recruit and allocate the required workforce | Recruitment batches, completion, and onboarded headcount |
| Finance / inventory | Evaluate cash, cost, stock, and feasibility within enabled role contracts | Financial indicators, inventory state, and resource availability |

The scenario enables the applicable departments and functions. The [Skill directories](../agent/.claude/skills/) contain each role's instructions and action examples; the authoritative action behavior is implemented by the corresponding business module.

## Information and coordination

Each firm has a private workspace. Role contexts combine authorized observations, recorded history, Analyst objectives where enabled, and configured coordination material. A private blackboard and department messages can communicate interdependent requirements within the enterprise.

Cross-enterprise information comes through the environment's market, exchange, and visibility interfaces. For example, the herding peer summary is an authorized aggregate with a configured lag; it is not permission to read another firm's private workspace.

The runtime keeps proposal generation separate from state ownership. Editing a proposed JSON action does not itself change cash, stock, staff, or an order.

## Decision-to-receipt loop

1. Prepare the current role's facts, objectives, available action contracts, and permitted history.
2. Invoke the Skill and capture the decision output.
3. Validate the output structure, actions, and required fields.
4. Submit eligible actions through the business environment.
5. Record receipts and give the role actionable failure feedback when a repair attempt is permitted.
6. Preserve the final task/attempt status and business outcomes for later review.

For example, a staffing-constrained firm may diagnose missing production labor, recruit a specified number of workers, wait for the recorded onboarding delay, and then submit feasible production plans. The recruitment receipt establishes that a batch was accepted; the later HR state establishes how many people actually joined. These are separate observations in the execution chain.

## What changes in the ablations

| Condition | Diagnosis | Functional decisions | Shared coordination |
| --- | --- | --- | --- |
| Full / MA | Enterprise Analyst | Department Skills | Enabled |
| NA | Neutral interface in place of dynamic Analyst diagnosis | Department Skills | Enabled |
| NC | Enterprise Analyst | Department Skills using authorized facts | Additional shared coordination/messages removed |
| SA | Part of the unified enterprise decision | One enterprise LLM task covers enabled functions | Functions are consolidated in the enterprise context |

SA is one decision maker per enterprise task/phase, not necessarily one provider request for an entire run: feedback attempts and trading phases can require further calls. In multi-enterprise SA, firms remain separate participants.

[Matched FD experiments](experiments.md#fd1fd5-matched-ablation-design) fix their designated operating setup and retry contract. Economic SA uses its frozen C3 regime. The optional legacy economic MA presets have a different configuration history and are described separately in [the comparison guide](experiments.md#economic-architecture-comparisons).

## Failure-specific repair

The matched FD and economic SA presets allow up to three attempts per task with a 300-second per-attempt timeout by default. Failures supply feedback about invalid output, unavailable tools, rejected parameters, or business feasibility as applicable. Successful effects and unknown receipts must not be replayed as if they had failed.

Ordinary unresolved output/business failures are recorded and may allow the simulation to continue. Permanent authentication, permission, model-configuration, account-quota, and fatal tool-channel failures terminate the job. An indeterminate business mutation follows the quarantine protocol in [the architecture guide](architecture.md#concurrent-decisions-and-shared-state).

Review attempt success, valid final output, accepted/executed actions, and business-goal completion separately. A corrected action, a safe withdrawal, and completion of the original task are different outcomes.

## Models and templates

Operator mode uses an SDK-compatible gateway configured in `scripts/.env` or the process environment. The model API credential is separate from the operator token used by the website. Visitors need neither credential.

The files in [agent/workspace/](../agent/workspace/) are executable templates. [Single-enterprise setup JSON](../agent/static_commands/single_enterprise/) establishes starting states. [scripted_rule_runner.py](../agent/scripted_rule_runner.py) generates rule-based decisions through the business action path; it is distinct from fixed setup actions.

See [model setup](../README.md#agent-setup) for endpoint checks and [the interface guide](interface_and_artifacts.md) for inspecting decisions and feedback.
