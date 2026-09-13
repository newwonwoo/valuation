# Valuation agent instructions

## Goal and completion

A request such as `분석시작 <기업>` means complete the requested company analysis through the canonical orchestrator and deliver its verified investor report. A plan, successful function test, prepared JSON, queued work order, partial segment value or report text alone is not completion. Continue the already authorized research, input repair, execution and verification within the same task; do not wait for repeated “계속”, “다시” or “분석시작”. A status question does not cancel the active task.

For valuation runs, completion requires full material business scope, canonical audit/freeze, saved state, an existing versioned investor report, two matching SVG cards, a verified immutable bundle and a working delivery link. Read the generated report and verify its headline, assumptions, scenario values, probabilities when valid, value bridge and sources against that same run. If a requested probability-weighted result remains unavailable, explicitly identify that limitation; a conditional scenario report is not a completed calibrated expected-value request.

For code or instruction changes, complete implementation, proportionate verification and authorized repository publication. Re-execute the affected company and regenerate its artifacts when the request includes that company/output. A documentation-only task does not require a new company valuation. Never claim that instructions alone prove live end-to-end completion.

## Find only the relevant contract

- Runtime entrypoint: `.agents/skills/valuation-analysis/SKILL.md`; root `SKILL.md` remains byte-identical. Use root-relative paths from the repository root regardless of which copy was loaded.
- Research and missing-input recovery: `docs/RESEARCH_CAMPAIGN_RUNBOOK.md`; completion intent: `docs/RESEARCH_COMPLETION_DESIGN.md`.
- Runtime invariants, calibration, source isolation, revisions and delivery: applicable sections of `docs/VALUATION_AGENT_CONTRACTS.md` and `docs/VALUATION_RUNTIME_DETAILS.md`.
- KR setup: `docs/RUNBOOK_KR_LIVE.md`. Other jurisdictions require checking their actual provider/evaluator support; a KR fixture is not US live validation.
- Find a component: `docs/README_V05_OPERATOR_INDEX.md`. Read the module's implementation and relevant tests when asked how it works; comments and prior assistant claims are not implementation evidence.
- Architecture or methodology changes: relevant sections of `01_Rocketesla_Insight_Valuation_Framework.md`, `docs/V04_ROCKETSLA_EXTENSION.md`, `docs/V05_WORKFLOW_CONTRACT.md`, `docs/GENERIC_ENGINE_DESIGN.md`, `docs/SIGNAL_INTELLIGENCE_LAYER_V1.md`, or `docs/LIVE_VALIDATION_AND_CALIBRATION.md` according to the affected boundary. Do not load the whole stack for a small edit.

Detailed contracts remain mandatory when applicable. Operator supplements under `references/` cannot override canonical contracts or deterministic gates. Reconcile contradictory instructions against the latest intentional decision and executable behavior; do not select whichever permits a convenient number. The current source of stage order is `config/control_plane_stage_registry.yaml` and the orchestrator, not a copied list.

## Execution ownership and recovery

- The host model reads original sources and provides typed research, hypotheses, peer comparisons and assumptions; deterministic code owns calculations, compilation, probabilities, audit, freeze and reporting. Use the orchestrator's supported assisted/file-handoff path. Do not replace it with a chat-written valuation or a new LLM API dependency.
- Inspect emitted missing-input and staff work orders, supply responses yourself, and resume the supported coordinator in the same workspace. Do not ask the user to write JSON or repeat a command that the host can execute.
- Missing data first calls for primary-source research, then independent sources/discovery leads, comparable-business adjustments or evidence-supported bounds. Document assumptions and counterevidence. Never manufacture facts, probabilities, source receipts or successful statuses, silently use zero, or drop a material segment to get a result.
- Distinguish reparable input/format defects from a genuinely unsupported method, unresolved source contradiction or missing permission. Repair defects within the approved scope. If a material new capability needs development, use existing approval if it covers that capability; otherwise finish the concrete design and identify the exact decision needed. Do not disable gates or expand permissions to force completion.
- Respect implemented retry budgets. Change the source or repair method after a repeated failure. Do not reset counters to loop forever. If exhausted, save the actual completed work, unresolved cause and exact resume command; report a blocker honestly without certifying completion or replacing last-good state.
- Before declaring repository/source access unavailable, try the available authorized checkout/connector and inspect the actual error. Use a supported alternative access path when available. Do not claim connection failure from a missing local checkout or request new credentials reflexively.

## Revisions with minimal rework

Translate corrections into the smallest affected tasks and observable acceptance criteria. For nontrivial revisions use `revision_orchestration.py` and the Unit Contract impact graph; the impact graph is not an execution DAG. Keep disjoint writes and existing work claims; read `config/work_claims.yaml` before editing guarded paths. Use bounded parallel work where authorized and independent, with one owner for integration and final delivery.

Reuse unchanged research only when input/source/prompt versions still match. Invalidate changed tasks and dependent results. Follow the runner's supported re-entry point: selective research reuse does not authorize jumping into the middle of the 33-stage audit/freeze pipeline. Changed valuation inputs require downstream calculation and report regeneration. Copy/layout-only changes remain in reporting unless they introduce a material valuation claim.

A new investment claim must reach evidence → bridge → assumptions → cash flows/value and the same report. If value does not change, verify why and label it reference context; never change only the title or force a numerical delta. When the user says “민감도” in development planning, interpret the surrounding request: it may mean change impact and failure prevention, not a new investment sensitivity subsystem.

## Validation and publication

Keep calculations pure and deterministic, without hidden constants. Prefer small typed functions; preserve OCI regression and existing protected runtime contracts. Never commit secrets, paid report bodies, personal positions or private state.

- Documentation/skill-only changes: check canonical-copy equality, frontmatter, relevant links, conflicting instructions and realistic behavior. Do not add tests that merely assert wording. Existing CI gates remain in force.
- Model/runtime changes: first run affected tests and resolve failures caused by the change; then run the full existing pytest/audit/OCI gate once on the final implementation before publication/merge. Expand retesting only for a concrete changed risk or required gate. Do not repeatedly restart the entire suite for an unchanged implementation.
- “반영” includes saving the relevant change to the repository. Apply the session's existing publication/merge authorization and repository protections; do not ask for an already granted approval. Commit, remote publication, merge and CI completion are distinct states and must be reported accurately. Preserve unrelated concurrent work and never force-push to bypass it.

## Communication

Use concise, plain Korean. State the result, remaining material issue and next action. Give meaningful progress during ongoing work, without asking permission to continue already authorized actions. Do not claim background work continues after the turn ends. The final answer links the actual deliverable and summarizes the outcome and limitations. Keep hashes, JSON, gate enums and orchestration details out of the investor-facing narrative; preserve them in the machine/audit artifacts. See the reporting section of `docs/VALUATION_AGENT_CONTRACTS.md` for full delivery requirements.
