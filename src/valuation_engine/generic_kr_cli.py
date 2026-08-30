"""The one-line entrance: ``분석시작 <회사>`` on the generic KR providers.

This is the glue between the CLI contract and the cold-start factory, so the
whole engine runs as:

.. code-block:: bash

   export DART_API_KEY=...                                   # OpenDART key
   export VALUATION_LLM_TRANSPORT=my_deploy.transport:build  # the model seat
   export VALUATION_METHOD=commodity_price_taker/normalized_multiple
   export VALUATION_UNDERWRITING_PATH=runs/hanbit/underwriting.yaml

   PYTHONPATH=src python -m valuation_engine.cli "분석시작 한빛제강" \\
       --provider-factory valuation_engine.generic_kr_cli:factory

A chat front end ("ㅇㅇ 분석해줘") is a thin dispatcher over exactly this
call. Two different LLM roles must never be conflated there:

- the **conversational operator** that parses the request and invokes this CLI
  holds no authority — it launches the run and hands back the engine's own
  report artifact, hashes intact, without paraphrasing numbers;
- the **staff seats inside the run** (hypotheses, red team, bridges, filing
  locators) come from ``VALUATION_LLM_TRANSPORT`` and operate proposal-only
  inside the authority boundary.

Deployment facts arrive from the environment because they are the caller's,
never the repository's: the OpenDART key, the model transport (a
``module:callable`` returning a ``ProposalTransport`` — the engine imports no
vendor SDK and holds no model credential), and the per-run declarations
(method choice, underwriting file, optional post-freeze market/street inputs).
Everything company-specific still arrives through the run itself.
"""

from __future__ import annotations

from datetime import date, timedelta
from importlib import import_module
import os

from .cli_runtime import LiveAnalysisRequest
from .generic_live_providers import (
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
)
from .kr_opendart_provider import OpenDartFilingSelection, OpenDartNetwork
from .live_indexers import HttpTransport, require_env_credential
from .llm_transport import ProposalTransport
from .valuation_plan_compiler import SegmentMethodChoice


class GenericCLIConfigError(ValueError):
    """Raised when the environment does not carry a complete run declaration."""


def _load_transport() -> ProposalTransport:
    spec = os.environ.get("VALUATION_LLM_TRANSPORT", "").strip()
    if not spec or ":" not in spec:
        raise GenericCLIConfigError(
            "VALUATION_LLM_TRANSPORT must name a 'module:callable' returning a "
            "ProposalTransport; the engine ships no model binding and holds no "
            "model credential"
        )
    module_name, _, attribute = spec.partition(":")
    try:
        builder = getattr(import_module(module_name), attribute)
        transport = builder()
    except Exception as exc:
        raise GenericCLIConfigError(
            f"VALUATION_LLM_TRANSPORT {spec!r} could not be constructed: "
            f"{type(exc).__name__}"
        ) from exc
    if not isinstance(transport, ProposalTransport):
        raise GenericCLIConfigError(
            f"VALUATION_LLM_TRANSPORT {spec!r} did not return a ProposalTransport"
        )
    return transport


def _method_choices(segment_id: str) -> tuple[SegmentMethodChoice, ...]:
    raw = os.environ.get("VALUATION_METHOD", "").strip()
    if not raw:
        raise GenericCLIConfigError(
            "VALUATION_METHOD is required as 'archetype/method[/version]'; "
            "choosing a valuation method is analyst intent the runtime demands "
            "explicitly"
        )
    parts = raw.split("/")
    if len(parts) not in (2, 3) or not all(parts):
        raise GenericCLIConfigError(
            f"VALUATION_METHOD {raw!r} must be 'archetype/method[/version]'"
        )
    version = parts[2] if len(parts) == 3 else None
    return (SegmentMethodChoice(segment_id, parts[0], parts[1], version),)


def _filing_selection(as_of: str, segment_id: str) -> OpenDartFilingSelection:
    cutoff = date.fromisoformat(as_of[:10])
    default_year = cutoff.year - 1
    business_year = os.environ.get("VALUATION_BUSINESS_YEAR", str(default_year))
    report_code = os.environ.get("VALUATION_REPORT_CODE", "11011")
    fiscal_period_end = os.environ.get(
        "VALUATION_FISCAL_PERIOD_END", f"{business_year}-12-31"
    )
    return OpenDartFilingSelection(
        business_year=business_year,
        report_code=report_code,
        fiscal_period_end=fiscal_period_end,
        checked_at=as_of,
        segment_id=segment_id,
    )


def _optional(name: str) -> str | None:
    value = os.environ.get(name, "").strip()
    return value or None


def factory(request: LiveAnalysisRequest):
    """LiveRuntimeConfigFactory for the CLI: environment in, attested run out."""
    api_key = require_env_credential("DART_API_KEY")
    as_of = os.environ.get("VALUATION_AS_OF", date.today().isoformat())
    segment_id = "core"
    scenarios = tuple(
        item.strip()
        for item in os.environ.get("VALUATION_SCENARIOS", "Base").split(",")
        if item.strip()
    )
    transport = _load_transport()
    http = HttpTransport(
        timeout_seconds=float(os.environ.get("VALUATION_HTTP_TIMEOUT", "20")),
    )
    network = OpenDartNetwork.from_http_transport(http, api_key=api_key)
    spec = GenericKRRuntimeSpec(
        as_of=as_of,
        scenario_ids=scenarios,
        method_choices=_method_choices(segment_id),
        filing=_filing_selection(as_of, segment_id),
        forecast_years=int(os.environ.get("VALUATION_FORECAST_YEARS", "5")),
        declared_underwriting_path=_optional("VALUATION_UNDERWRITING_PATH"),
        declared_risk_path=_optional("VALUATION_RISK_PACK_PATH"),
        market_config_path=_optional("VALUATION_MARKET_CONFIG"),
        street_export_path=_optional("VALUATION_STREET_EXPORT"),
        market_currency=_optional("VALUATION_MARKET_CURRENCY")
        or ("KRW" if _optional("VALUATION_MARKET_CONFIG") else None),
    )
    return build_generic_kr_runtime_factory(
        network=network,
        transport=transport,
        spec=spec,
    )(request)
