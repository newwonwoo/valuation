# Runtime Context Hardening

Canonical stage adapters operate under four fail-closed boundaries:

1. The top-level runtime data mapping is read-only for the adapter.
2. Mutable built-in containers are isolated per stage; an attempted in-place change blocks the stage and cannot alter canonical upstream state.
3. EvidenceLedger is temporarily sealed against append while downstream adapters execute.
4. Adapter exceptions and rationales are sanitized before they enter persisted StageTrace or blocked reasons.

Stage-control fields (`run_id`, execution mode, traces, Freeze token, data binding) are disposable on the adapter view and any attempted mutation is rejected. A stage must return `StageExecutionResult`; other return types are contract violations.

The existing append-only output rule remains authoritative: a stage cannot overwrite an already-owned canonical context key.
