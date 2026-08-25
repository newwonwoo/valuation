from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum
from math import isfinite
from urllib.parse import urlparse

from .authorized_primary_sources import PrimaryMetricObservation
from .dart_facts import DartAmountBasis, DartFactMetricSpec
from .sec_edgar import SECMetricSpec


class CompanyKPIProviderKind(str, Enum):
    OPENDART_FACT = "opendart_fact"
    SEC_COMPANYFACT = "sec_companyfact"
    EXACT_PRIMARY_DOCUMENT = "exact_primary_document"


@dataclass(frozen=True)
class ExactDocumentMetricSpec:
    metric: str
    segment: str
    locator: str
    unit: str
    allowed_hosts: tuple[str, ...]
    evidence_role: str
    critical: bool = False

    def validate(self) -> None:
        if not all(
            (
                self.metric,
                self.segment,
                self.locator,
                self.unit,
                self.allowed_hosts,
                self.evidence_role,
            )
        ):
            raise ValueError("exact document KPI spec is incomplete")
        if len(self.allowed_hosts) != len(set(self.allowed_hosts)):
            raise ValueError(f"duplicate allowed hosts for KPI {self.metric}")
        if self.evidence_role not in {"realized", "company_plan", "policy"}:
            raise ValueError(f"unsupported KPI evidence_role: {self.evidence_role}")
        if any(token in self.metric.casefold() for token in ("target_price", "market_price", "consensus")):
            raise ValueError("target-market KPI names are forbidden")


@dataclass(frozen=True)
class CanonicalCompanyKPIProfile:
    company_id: str
    display_name: str
    jurisdiction: str
    resolver_identity: str
    sec_cik: str | None
    opendart_resolver_required: bool
    sec_fact_specs: tuple[SECMetricSpec, ...] = ()
    dart_fact_specs: tuple[DartFactMetricSpec, ...] = ()
    document_specs: tuple[ExactDocumentMetricSpec, ...] = ()

    def validate(self) -> None:
        if not all((self.company_id, self.display_name, self.jurisdiction, self.resolver_identity)):
            raise ValueError("canonical company KPI profile is incomplete")
        if self.sec_cik is not None:
            if len(self.sec_cik) != 10 or not self.sec_cik.isdigit():
                raise ValueError(f"{self.company_id} SEC CIK must be exactly 10 digits")
            if self.jurisdiction != "US":
                raise ValueError("SEC KPI profiles must use US jurisdiction")
        if self.opendart_resolver_required and self.jurisdiction != "KR":
            raise ValueError("OpenDART resolver requirement is valid only for KR profiles")
        if not (self.sec_fact_specs or self.dart_fact_specs or self.document_specs):
            raise ValueError(f"{self.company_id} has no KPI extraction coverage")
        metrics: list[str] = []
        for spec in self.sec_fact_specs:
            spec.validate()
            metrics.append(spec.metric)
        for spec in self.dart_fact_specs:
            spec.validate()
            metrics.append(spec.metric)
        for spec in self.document_specs:
            spec.validate()
            metrics.append(spec.metric)
        if len(metrics) != len(set(metrics)):
            raise ValueError(f"{self.company_id} has duplicate KPI metric names")


@dataclass(frozen=True)
class ExactDocumentMetricCandidate:
    company_id: str
    metric: str
    source_ref: str
    locator: str
    value: str | int | float | Decimal
    unit: str
    effective_date: str


def compile_exact_document_observation(
    candidate: ExactDocumentMetricCandidate,
    *,
    registry: tuple[CanonicalCompanyKPIProfile, ...] | None = None,
) -> PrimaryMetricObservation:
    profiles = registry or CANONICAL_COMPANY_KPI_REGISTRY
    profile = _profile(candidate.company_id, profiles)
    matches = tuple(spec for spec in profile.document_specs if spec.metric == candidate.metric)
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one document KPI spec for {candidate.company_id}/{candidate.metric}"
        )
    spec = matches[0]
    spec.validate()
    host = (urlparse(candidate.source_ref).hostname or "").casefold()
    if host not in {item.casefold() for item in spec.allowed_hosts}:
        raise ValueError(
            f"KPI {candidate.metric} source host {host!r} is outside the authorized company source set"
        )
    _validate_registered_sec_issuer(profile, candidate.source_ref, candidate.metric)
    if candidate.locator != spec.locator:
        raise ValueError(
            f"KPI {candidate.metric} locator must match the registered exact locator"
        )
    if candidate.unit != spec.unit:
        raise ValueError(
            f"KPI {candidate.metric} unit mismatch: expected {spec.unit}, got {candidate.unit}"
        )
    _validate_numeric_candidate(candidate.value, candidate.metric)
    return PrimaryMetricObservation(
        metric=spec.metric,
        segment=spec.segment,
        value=candidate.value,
        unit=spec.unit,
        effective_date=candidate.effective_date,
        locator=spec.locator,
        critical=spec.critical,
        notes=(
            f"canonical company KPI registry; company={profile.company_id}; "
            f"role={spec.evidence_role}; exact locator required"
        ),
        evidence_role=spec.evidence_role,
        source_ref=candidate.source_ref,
    )


def profile_for(company_id: str) -> CanonicalCompanyKPIProfile:
    return _profile(company_id, CANONICAL_COMPANY_KPI_REGISTRY)


def _profile(
    company_id: str,
    profiles: tuple[CanonicalCompanyKPIProfile, ...],
) -> CanonicalCompanyKPIProfile:
    matches = tuple(item for item in profiles if item.company_id == company_id)
    if len(matches) != 1:
        raise ValueError(f"unknown canonical company KPI profile: {company_id}")
    return matches[0]


def _validate_registered_sec_issuer(
    profile: CanonicalCompanyKPIProfile,
    source_ref: str,
    metric: str,
) -> None:
    if profile.sec_cik is None:
        return
    parsed = urlparse(source_ref)
    parts = tuple(part for part in parsed.path.split("/") if part)
    expected_cik = str(int(profile.sec_cik))
    if (
        len(parts) < 5
        or tuple(part.casefold() for part in parts[:3]) != ("archives", "edgar", "data")
        or parts[3] != expected_cik
    ):
        raise ValueError(
            f"KPI {metric} SEC source CIK must match registered issuer {profile.sec_cik}"
        )


def _validate_numeric_candidate(value: object, metric: str) -> None:
    if isinstance(value, bool) or value is None:
        raise ValueError(f"KPI {metric} requires a numeric value")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"KPI {metric} value must be finite")
        return
    if isinstance(value, (int, float)):
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"KPI {metric} value must be finite")
        return
    if isinstance(value, str):
        try:
            parsed = Decimal(value.replace(",", ""))
        except Exception as exc:
            raise ValueError(f"KPI {metric} string value must be numeric") from exc
        if not parsed.is_finite():
            raise ValueError(f"KPI {metric} value must be finite")
        return
    raise TypeError(f"KPI {metric} value must be a finite numeric scalar")


CANONICAL_COMPANY_KPI_REGISTRY: tuple[CanonicalCompanyKPIProfile, ...] = (
    CanonicalCompanyKPIProfile(
        company_id="OCI_HOLDINGS",
        display_name="OCI Holdings",
        jurisdiction="KR",
        resolver_identity="OpenDART exact company resolver",
        sec_cik=None,
        opendart_resolver_required=True,
        dart_fact_specs=(
            DartFactMetricSpec(
                metric="revenue",
                account_ids=("ifrs-full_Revenue", "ifrs_Revenue"),
                statement_divisions=("IS", "CIS"),
                critical=True,
                amount_basis=DartAmountBasis.YEAR_TO_DATE,
            ),
            DartFactMetricSpec(
                metric="operating_income",
                account_ids=("dart_OperatingIncomeLoss",),
                statement_divisions=("IS", "CIS"),
                critical=True,
                amount_basis=DartAmountBasis.YEAR_TO_DATE,
            ),
        ),
        document_specs=(
            ExactDocumentMetricSpec(
                metric="solar_grade_polysilicon_capacity",
                segment="renewable_energy",
                locator="Solar-grade polysilicon capacity expansion (2027)",
                unit="kMT",
                allowed_hosts=("www.oci-holdings.co.kr", "web-static.oci-holdings.co.kr"),
                evidence_role="company_plan",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="electronic_grade_polysilicon_capacity",
                segment="advanced_materials",
                locator="Electronic-grade polysilicon 8,000 MT production (2026)",
                unit="kMT",
                allowed_hosts=("www.oci-holdings.co.kr", "web-static.oci-holdings.co.kr"),
                evidence_role="company_plan",
            ),
            ExactDocumentMetricSpec(
                metric="us_solar_cell_capacity",
                segment="renewable_energy",
                locator="Investing in 2 GW cell capacity",
                unit="GW",
                allowed_hosts=("www.oci-holdings.co.kr", "web-static.oci-holdings.co.kr"),
                evidence_role="company_plan",
            ),
        ),
    ),
    CanonicalCompanyKPIProfile(
        company_id="ORACLE",
        display_name="Oracle",
        jurisdiction="US",
        resolver_identity="SEC CIK 0001341439",
        sec_cik="0001341439",
        opendart_resolver_required=False,
        sec_fact_specs=(
            SECMetricSpec("revenue", "us-gaap", "Revenues", "USD", "company", critical=True),
        ),
        document_specs=(
            ExactDocumentMetricSpec(
                metric="remaining_performance_obligations",
                segment="cloud_and_software",
                locator="Remaining Performance Obligations from Contracts with Customers",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="customer_prepayments_significant_financing",
                segment="cloud_and_software",
                locator="Customer Prepayments and Sales of Financing Receivables",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="cloud_infrastructure_revenue",
                segment="cloud_and_software",
                locator="Cloud infrastructure",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
            ),
        ),
    ),
    CanonicalCompanyKPIProfile(
        company_id="BLOOM_ENERGY",
        display_name="Bloom Energy",
        jurisdiction="US",
        resolver_identity="SEC CIK 0001664703",
        sec_cik="0001664703",
        opendart_resolver_required=False,
        sec_fact_specs=(
            SECMetricSpec("revenue", "us-gaap", "Revenues", "USD", "company", critical=True),
        ),
        document_specs=(
            ExactDocumentMetricSpec(
                metric="product_revenue",
                segment="energy_servers",
                locator="Product Revenue",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="installation_revenue",
                segment="energy_servers",
                locator="Installation Revenue",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
            ),
            ExactDocumentMetricSpec(
                metric="service_revenue",
                segment="energy_servers",
                locator="Service Revenue",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
            ),
        ),
    ),
    CanonicalCompanyKPIProfile(
        company_id="GE_VERNOVA",
        display_name="GE Vernova",
        jurisdiction="US",
        resolver_identity="SEC CIK 0001996810",
        sec_cik="0001996810",
        opendart_resolver_required=False,
        sec_fact_specs=(
            SECMetricSpec("revenue", "us-gaap", "Revenues", "USD", "company", critical=True),
        ),
        document_specs=(
            ExactDocumentMetricSpec(
                metric="remaining_performance_obligations",
                segment="company",
                locator="RPO, a measure of backlog",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="gas_power_equipment_backlog_and_slot_reservations",
                segment="power",
                locator="Gas Power equipment backlog and slot reservation agreements",
                unit="GW",
                allowed_hosts=("www.sec.gov",),
                evidence_role="company_plan",
                critical=True,
            ),
            ExactDocumentMetricSpec(
                metric="orders",
                segment="company",
                locator="Orders",
                unit="USD",
                allowed_hosts=("www.sec.gov",),
                evidence_role="realized",
            ),
        ),
    ),
)


def validate_canonical_company_kpi_registry() -> None:
    company_ids = tuple(item.company_id for item in CANONICAL_COMPANY_KPI_REGISTRY)
    if company_ids != ("OCI_HOLDINGS", "ORACLE", "BLOOM_ENERGY", "GE_VERNOVA"):
        raise ValueError("canonical company KPI registry order/coverage drift")
    if len(company_ids) != len(set(company_ids)):
        raise ValueError("canonical company KPI registry contains duplicate company IDs")
    for profile in CANONICAL_COMPANY_KPI_REGISTRY:
        profile.validate()
