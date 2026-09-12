"""Replayable Broker Research discovery inputs for generic live runs.

The file contains short, paraphrased discovery claims and links only.  It does
not archive licensed report bodies, and target-company forecasts, target
prices, ratings, consensus and target multiples remain forbidden before the
intrinsic-value freeze.  Company-specific observations may only open a
metric-backed request that the primary-evidence collectors must verify.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .broker_research import BrokerClaim, BrokerFieldClass, BrokerReportType
from .broker_runtime import (
    BrokerResearchBatch,
    BrokerResearchObservation,
    BrokerResearchLoader,
)


class DeclaredBrokerResearchError(ValueError):
    """Raised when a prepared Broker Research discovery file is invalid."""


def _tuple_of_strings(value: object, *, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise DeclaredBrokerResearchError(f"{label} must be a list of strings")
    return tuple(item.strip() for item in value)


def declared_broker_research_loader(
    path: str | Path,
    *,
    run_as_of: str,
) -> BrokerResearchLoader:
    """Load and validate a prepared discovery snapshot once, then replay it."""

    source_path = Path(path)
    try:
        payload = yaml.safe_load(source_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DeclaredBrokerResearchError(
            f"cannot read declared Broker Research file: {source_path}"
        ) from exc
    if not isinstance(payload, dict):
        raise DeclaredBrokerResearchError(
            "declared Broker Research file must be a mapping"
        )

    rows = payload.get("observations")
    if not isinstance(rows, list) or not rows:
        raise DeclaredBrokerResearchError(
            "declared Broker Research requires observations"
        )
    observations: list[BrokerResearchObservation] = []
    for index, raw in enumerate(rows):
        if not isinstance(raw, dict):
            raise DeclaredBrokerResearchError(
                f"observations[{index}] must be a mapping"
            )
        label = f"observations[{index}]"
        try:
            claim = BrokerClaim(
                claim_id=str(raw["claim_id"]).strip(),
                source_id=str(raw["source_id"]).strip(),
                broker_family=str(raw["broker_family"]).strip(),
                report_type=BrokerReportType(str(raw["report_type"]).strip()),
                field_class=BrokerFieldClass(str(raw["field_class"]).strip()),
                industry_node=str(raw["industry_node"]).strip(),
                statement=str(raw["statement"]).strip(),
                target_company_specific=bool(raw.get("target_company_specific", False)),
                underlying_data_families=_tuple_of_strings(
                    raw.get("underlying_data_families"),
                    label=f"{label}.underlying_data_families",
                ),
                report_date=str(raw["report_date"]).strip(),
            )
            source_ref = str(raw["source_ref"]).strip()
            if not source_ref.startswith(("http://", "https://")):
                raise DeclaredBrokerResearchError(
                    f"{label}.source_ref must be an HTTP URL"
                )
            observations.append(
                BrokerResearchObservation(
                    claim=claim,
                    segment_id=str(raw["segment_id"]).strip(),
                    source_ref=source_ref,
                    verification_metrics=_tuple_of_strings(
                        raw.get("verification_metrics"),
                        label=f"{label}.verification_metrics",
                    ),
                    verification_requests=_tuple_of_strings(
                        raw.get("verification_requests"),
                        label=f"{label}.verification_requests",
                    ),
                    primary_source_hints=_tuple_of_strings(
                        raw.get("primary_source_hints"),
                        label=f"{label}.primary_source_hints",
                    ),
                )
            )
        except KeyError as exc:
            raise DeclaredBrokerResearchError(
                f"{label} is missing {exc.args[0]}"
            ) from exc
        except (TypeError, ValueError) as exc:
            if isinstance(exc, DeclaredBrokerResearchError):
                raise
            raise DeclaredBrokerResearchError(f"invalid {label}: {exc}") from exc

    source_refs = _tuple_of_strings(
        payload.get("source_refs"), label="source_refs"
    )
    batch = BrokerResearchBatch(
        checked_at=str(payload.get("checked_at") or "").strip(),
        observations=tuple(observations),
        source_refs=source_refs,
    )
    try:
        batch.validate(data_cutoff=run_as_of)
    except (TypeError, ValueError) as exc:
        raise DeclaredBrokerResearchError(
            f"declared Broker Research failed validation: {exc}"
        ) from exc

    def load(_context) -> BrokerResearchBatch:
        return batch

    return load


__all__ = [
    "DeclaredBrokerResearchError",
    "declared_broker_research_loader",
]
