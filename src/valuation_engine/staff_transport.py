"""Prompt-bound host-model handoff, with an explicit deterministic replay mode."""
from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import re
from uuid import uuid4

from .llm_transport import TransportError


class StaffWorkRequired(TransportError):
    """A host model must provide a new answer before the run can continue."""


class StaffTransport:
    def __init__(self, staff_dir, *, mode="replay", request_dir=None, audit_dir=None,
                 live_transport=None):
        if mode not in {"replay", "assisted", "live"}:
            raise ValueError("staff mode must be replay, assisted, or live")
        self.staff_dir = Path(staff_dir)
        self.mode = mode
        self.request_dir = Path(request_dir) if request_dir else self.staff_dir / "requests"
        self.audit_dir = Path(audit_dir) if audit_dir else self.request_dir / "audit"
        self._live = live_transport
        self._counts = {}
        self._attempt = 0
        self._session_id = uuid4().hex

    def supports_role(self, role):
        return (self.mode != "replay" or (self.staff_dir / f"{role}.json").is_file()
                or bool(self._live) or bool(os.environ.get("VALUATION_LLM_TRANSPORT", "").strip()))

    def _live_transport(self):
        if self._live is None:
            from .generic_kr_cli import _load_transport
            self._live = _load_transport()
        return self._live

    @staticmethod
    def _write(path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)

    def complete(self, *, role, prompt):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", role):
            raise ValueError("invalid staff role")
        digest = sha256(prompt.encode("utf-8")).hexdigest()
        key = f"{role}.{digest}"
        self._attempt += 1
        audit_key = f"{key}.{self._session_id}.{self._attempt}"
        response_path = self.staff_dir / "responses" / f"{key}.json"
        request = {"schema_version": "staff-request/v1", "role": role,
                   "prompt_sha256": digest, "prompt": prompt,
                   "response_path": str(response_path.resolve()), "mode": self.mode}
        self._write(self.audit_dir / f"{audit_key}.request.json", request)
        reason = "no staff proposal file"
        answer = None
        origin = None
        # Live mode always asks its provider; assisted accepts only prompt-bound files.
        if self.mode != "live":
            path = self.staff_dir / f"{role}.json" if self.mode == "replay" else response_path
            if path.is_file():
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (ValueError, OSError) as exc:
                    request["reason"] = f"invalid staff response: {type(exc).__name__}"
                    request_path = self.request_dir / f"{key}.json"
                    self._write(request_path, request)
                    raise StaffWorkRequired(f"WORK_REQUIRED: invalid staff response; request: {request_path}") from exc
                turns = payload if isinstance(payload, list) else [payload]
                index = self._counts.get(str(path), 0)
                if index < len(turns):
                    selected = turns[index]
                    self._counts[str(path)] = index + 1
                    if isinstance(selected, dict) and selected.get("schema_version") == "staff-response/v1":
                        if selected.get("role") != role or selected.get("prompt_sha256") != digest:
                            reason = "stale staff response: role or prompt hash mismatch"
                        elif "response" not in selected:
                            reason = "staff response envelope lacks response"
                        else:
                            answer = selected["response"]
                    elif self.mode == "replay":
                        answer = selected
                    else:
                        reason = "assisted mode requires a prompt-bound staff-response/v1 envelope"
                    origin = str(path)
                else:
                    reason = "staff proposal responses exhausted; fresh repair answer required"
        has_live = self._live is not None or bool(os.environ.get("VALUATION_LLM_TRANSPORT", "").strip())
        # Replay never silently changes to a live answer after a declared script is exhausted.
        replay_missing = not (self.staff_dir / f"{role}.json").is_file()
        if answer is None and has_live and (self.mode != "replay" or replay_missing):
            answer = self._live_transport().complete(role=role, prompt=prompt)
            origin = "live_transport"
        if answer is None:
            request["reason"] = reason
            request_path = self.request_dir / f"{key}.json"
            self._write(request_path, request)
            raise StaffWorkRequired(f"WORK_REQUIRED: {reason}; write a prompt-bound response to "
                                    f"{response_path}; request: {request_path}")
        result = answer if isinstance(answer, str) else json.dumps(answer, ensure_ascii=False)
        self._write(self.audit_dir / f"{audit_key}.response.json", {
            "schema_version": "staff-response/v1", "role": role,
            "prompt_sha256": digest, "response": answer, "origin": origin})
        return result
