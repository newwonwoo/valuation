# Chat Front-End Contract

`src/valuation_engine/chat_dispatch.py`

The one-line UX is "ㅇㅇ 분석해줘". Behind it, a conversational model turns the
request into a run and shows the result. This is the last mile the engine's
containment must survive, and the mechanism that makes it survive is a
byte-checked handoff, not a politeness rule.

## The flow

```
"삼성전자 분석해줘"
   │  extract_company / to_analysis_command   (parse only; no authority)
   ▼
"분석시작 삼성전자"
   │  dispatch_analysis → execute_live_analysis (the attested pipeline)
   ▼
ReportHandoff { report_text, report_sha256 }
   │  the conversational model frames AROUND this, never inside it
   ▼
verify_report_presentation(handoff, presented_body)   ← refuses any drift
```

## The rule, made enforceable

The conversational model **never re-states the numbers**. It may add framing —
a greeting, a "여기 결과입니다", a pointer to a section — but the report body it
presents must equal the sealed artifact byte for byte.
`verify_report_presentation` re-fingerprints whatever the chat layer is about to
send for the body and raises on any mismatch: a single altered digit changes the
SHA-256. This turns the last-mile rule from documentation into a check a caller
runs before sending.

`ReportHandoff.fenced()` gives the ready form: the report in a fenced block with
`<!-- report_sha256=… -->` appended, so a UI can display the artifact and a
verifier can confirm it.

## What the dispatcher is and is not

- It **parses** a company out of free text (a 6-digit KRX code wins; otherwise
  the request verbs are stripped and the residue is the company) and **runs**
  the engine. It never guesses a company — an empty residue is an error.
- It holds **no authority**: it does not choose the valuation method, read a
  filing, or touch a number. Method, as_of and the operator's underwriting are
  the deployment declarations of `generic_kr_cli` (environment / operator
  files), each still checked inside the run.
- A **blocked run** hands back its block codes and no number — the same artifact
  the CLI prints. The chat layer presents that verbatim too: "이 종목은 …가
  없어 차단되었습니다" is honest; inventing a number to be helpful is the exact
  failure the whole system exists to prevent.

## Why a conversational model is safe here at all

Every way an LLM mis-handles a number (transcription slip, wrong column,
forecast-as-fact, semantic swap — see `docs/LLM_CONTAINMENT_THREAT_MODEL.md`)
requires the model to *emit* the number. The chat layer never does: it emits
framing around an artifact whose bytes are fixed upstream and checked
downstream. Intelligence in the model, the number from the sealed run.
