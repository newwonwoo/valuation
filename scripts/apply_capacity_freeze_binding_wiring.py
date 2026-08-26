from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "src" / "valuation_engine" / "live_runtime.py"
REPORT = ROOT / "src" / "valuation_engine" / "report_form.py"
DOC = ROOT / "docs" / "CAPACITY_COMMITMENT_GATE.md"


def replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f"{label}: expected one replacement, found {count}")
    return text.replace(old, new, 1)


def patch_runtime() -> None:
    text = RUNTIME.read_text(encoding="utf-8")
    if "from .capacity_freeze_binding import capacity_freeze_binding_adapter" not in text:
        marker = "from .capacity_runtime_integrity import (\n"
        text = replace_once(
            text,
            marker,
            "from .capacity_freeze_binding import capacity_freeze_binding_adapter\n"
            + marker,
            label="runtime import",
        )
    old = '''    street_load = (\n        street_reference_load_adapter(loader=providers.street_loader)\n        if providers.street_loader is not None\n        else _unavailable_stage("Street reference")\n    )\n'''
    if "street_provider_load" not in text:
        new = '''    street_provider_load = (\n        street_reference_load_adapter(loader=providers.street_loader)\n        if providers.street_loader is not None\n        else _unavailable_stage("Street reference")\n    )\n    street_load = chain_stage_adapters(\n        capacity_freeze_binding_adapter(),\n        street_provider_load,\n    )\n'''
        text = replace_once(text, old, new, label="Street boundary")
    RUNTIME.write_text(text, encoding="utf-8")


def patch_report() -> None:
    text = REPORT.read_text(encoding="utf-8")
    if '"capacity_freeze_binding_hash",' not in text:
        text = replace_once(
            text,
            '    "capacity_audit_hash",\n',
            '    "capacity_audit_hash",\n    "capacity_freeze_binding_hash",\n',
            label="report hash key",
        )
    if "capacity_freeze_binding_hash_present" not in text:
        text = replace_once(
            text,
            '                ("capacity_audit_hash_present", bool(data.get("capacity_audit_hash"))),\n',
            '                ("capacity_audit_hash_present", bool(data.get("capacity_audit_hash"))),\n'
            '                ("capacity_freeze_binding_hash_present", bool(data.get("capacity_freeze_binding_hash"))),\n',
            label="report attestation",
        )
    if "Capacity Freeze binding hash" not in text:
        text = replace_once(
            text,
            '        "| Capacity audit hash | `{{ capacity_audit_hash }}` |",\n',
            '        "| Capacity audit hash | `{{ capacity_audit_hash }}` |",\n'
            '        "| Capacity Freeze binding hash | `{{ capacity_freeze_binding_hash }}` |",\n',
            label="report template",
        )
    REPORT.write_text(text, encoding="utf-8")


def patch_doc() -> None:
    text = DOC.read_text(encoding="utf-8")
    heading = "## Freeze binding certificate"
    if heading not in text:
        text += '''\n\n## Freeze binding certificate\n\nImmediately after `INTRINSIC_VALUE_FREEZE` and before `STREET_REFERENCE_LOAD`, the runtime creates `CapacityFreezeBindingResult`. Its hash combines the frozen Capacity assessment, the blocking Capacity Audit hash and the immutable Freeze-token identity. Any change to the audit result or Freeze token changes the certificate. A capacity report cannot be labelled `VERIFIED_FROZEN` without this certificate. This supplements the canonical Freeze token without allowing post-Freeze Street or market data to mutate intrinsic value.\n'''
        DOC.write_text(text, encoding="utf-8")


def main() -> int:
    patch_runtime()
    patch_report()
    patch_doc()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
