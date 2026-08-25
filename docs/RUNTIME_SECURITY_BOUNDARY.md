# Runtime security boundary

Runtime adapters may read canonical typed inputs and return new stage-owned outputs. They may not mutate upstream values, runtime control fields, EvidenceLedger history, or disclose credentials through persisted failure text.
