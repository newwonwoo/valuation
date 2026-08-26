from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from pathlib import Path
from typing import Literal

from .cli_runtime import (
    LiveAnalysisRequest,
    build_live_runtime_config,
    load_live_runtime_config_factory,
    resolve_provider_factory_spec,
)
from .live_company_artifact import (
    SourceDocumentLineage,
    serialize_live_company_blocked,
    serialize_live_company_success,
)
from .live_runtime import run_prism


CaptureMode = Literal["success", "blocked"]


@dataclass(frozen=True)
class LiveCompanyCaptureRequest:
    company_id: str
    company_query: str
    jurisdiction: str
    state_root: Path
    run_id: str
    mode: CaptureMode
    provider_factory_spec: str | None = None
    source_documents: tuple[SourceDocumentLineage, ...] = ()
    adversarial_case_id: str = ""
    expected_reason_contains: str = ""

    def validate(self) -> None:
        if not all(
            (
                self.company_id,
                self.company_query,
                self.jurisdiction,
                str(self.state_root),
                self.run_id,
            )
        ):
            raise ValueError("live company capture request is incomplete")
        if self.mode not in {"success", "blocked"}:
            raise ValueError(f"unsupported live company capture mode: {self.mode!r}")
        if self.mode == "success":
            if not self.source_documents:
                raise ValueError("success capture requires source document lineage")
            for document in self.source_documents:
                document.validate()
        elif not all((self.adversarial_case_id, self.expected_reason_contains)):
            raise ValueError(
                "blocked capture requires adversarial_case_id and expected_reason_contains"
            )


def capture_live_company_fixture(request: LiveCompanyCaptureRequest) -> dict:
    """Run the operator-supplied LIVE_PRIMARY factory and serialize its actual result."""
    request.validate()
    factory_spec = resolve_provider_factory_spec(request.provider_factory_spec)
    factory = load_live_runtime_config_factory(factory_spec)
    analysis_request = LiveAnalysisRequest(
        command=f"분석시작 {request.company_query}",
        company_query=request.company_query,
        state_root=request.state_root,
        run_id=request.run_id,
        jurisdiction=request.jurisdiction,
    )
    config = build_live_runtime_config(analysis_request, factory)
    result = run_prism(config)
    if request.mode == "success":
        return serialize_live_company_success(
            result,
            company_id=request.company_id,
            source_documents=request.source_documents,
        )
    return serialize_live_company_blocked(
        result,
        company_id=request.company_id,
        adversarial_case_id=request.adversarial_case_id,
        expected_reason_contains=request.expected_reason_contains,
    )


def load_source_document_lineage(path: str | Path) -> tuple[SourceDocumentLineage, ...]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError("source lineage JSON must be a non-empty list")
    documents: list[SourceDocumentLineage] = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("source lineage rows must be JSON objects")
        document = SourceDocumentLineage(
            source_ref=str(row.get("source_ref") or ""),
            document_hash=str(row.get("document_hash") or ""),
            first_seen_at=str(row.get("first_seen_at") or ""),
        )
        document.validate()
        documents.append(document)
    if len({item.source_ref for item in documents}) != len(documents):
        raise ValueError("source lineage JSON contains duplicate source_ref values")
    return tuple(documents)


def write_live_company_fixture(
    artifact: dict,
    path: str | Path,
    *,
    overwrite: bool = False,
) -> str:
    destination = Path(path)
    if destination.exists() and not overwrite:
        raise FileExistsError(f"acceptance fixture already exists: {destination}")
    raw = json.dumps(
        artifact,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(raw)
    return sha256(raw).hexdigest()
