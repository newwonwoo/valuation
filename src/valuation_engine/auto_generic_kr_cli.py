"""One-line KR LIVE_PRIMARY factory with canonical method routing.

Unlike ``generic_kr_cli:factory``, this MCP-oriented factory does not require
``VALUATION_METHOD``. Industry DNA and the Module Requirement Plan prepare the
candidate evidence contract, while the existing ``VALUATION_METHOD_INTENT``
stage keeps sole authority to select a method when exactly one implemented
candidate remains. Genuine ambiguity still fails closed as
``AWAITING_USER_DECISION``.

If ``VALUATION_METHOD`` is explicitly supplied, the existing explicit-intent
factory remains authoritative and is used unchanged.
"""

from __future__ import annotations

from datetime import date
import os

from .auto_method_routing import enable_auto_method_routing
from .generic_kr_cli import (
    _filing_selection,
    _load_transport,
    _optional,
    factory as explicit_method_factory,
)
from .generic_live_providers import (
    GenericKRRuntimeSpec,
    build_generic_kr_runtime_factory,
)
from .kr_opendart_provider import OpenDartNetwork
from .live_indexers import HttpTransport, require_env_credential
from .method_capabilities import load_default_method_capability_registry
from .valuation_plan_compiler import SegmentMethodChoice


_PLACEHOLDER_ARCHETYPE = "commodity_price_taker"
_PLACEHOLDER_METHOD = "normalized_multiple"


def factory(request):
    """Build an attested KR runtime without pre-selecting a valuation method."""
    if os.environ.get("VALUATION_METHOD", "").strip():
        return explicit_method_factory(request)

    api_key = require_env_credential("DART_API_KEY")
    as_of = os.environ.get("VALUATION_AS_OF", date.today().isoformat())
    segment_id = "core"
    scenarios = tuple(
        item.strip()
        for item in os.environ.get("VALUATION_SCENARIOS", "Base").split(",")
        if item.strip()
    )
    if not scenarios:
        raise ValueError("VALUATION_SCENARIOS must contain at least one scenario")
    forecast_years = int(os.environ.get("VALUATION_FORECAST_YEARS", "5"))
    transport = _load_transport()
    http = HttpTransport(
        timeout_seconds=float(os.environ.get("VALUATION_HTTP_TIMEOUT", "20")),
    )
    network = OpenDartNetwork.from_http_transport(http, api_key=api_key)
    registry = load_default_method_capability_registry()

    # build_generic_kr_runtime_factory currently assembles source/risk/market
    # providers from an explicit declaration. Use a known supported method only
    # as a construction scaffold, then remove every method-owned contract before
    # returning the runtime. The placeholder never reaches the Control Plane.
    placeholder = (
        SegmentMethodChoice(
            segment_id,
            _PLACEHOLDER_ARCHETYPE,
            _PLACEHOLDER_METHOD,
        ),
    )
    base_spec = GenericKRRuntimeSpec(
        as_of=as_of,
        scenario_ids=scenarios,
        method_choices=placeholder,
        filing=_filing_selection(as_of, segment_id),
        forecast_years=forecast_years,
        declared_underwriting_path=_optional("VALUATION_UNDERWRITING_PATH"),
        declared_risk_path=_optional("VALUATION_RISK_PACK_PATH"),
        market_config_path=_optional("VALUATION_MARKET_CONFIG"),
        street_export_path=_optional("VALUATION_STREET_EXPORT"),
        market_currency=_optional("VALUATION_MARKET_CURRENCY")
        or ("KRW" if _optional("VALUATION_MARKET_CONFIG") else None),
    )
    base_factory = build_generic_kr_runtime_factory(
        network=network,
        transport=transport,
        spec=base_spec,
        capability_registry=registry,
    )
    auto_factory = enable_auto_method_routing(
        base_factory,
        forecast_years=forecast_years,
        scenario_ids=scenarios,
        capability_registry=registry,
    )
    return auto_factory(request)
