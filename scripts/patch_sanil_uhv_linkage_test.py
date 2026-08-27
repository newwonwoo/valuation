from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "tests" / "test_sanil_live_primary.py"
OLD = '''    assert linkage.hypothesis_ids == ("H:SANIL:CAPACITY", "H:SANIL:Core")\n'''
NEW = '''    assert linkage.hypothesis_ids == (\n        "H:SANIL:CAPACITY",\n        "H:SANIL:UHV_CAPACITY",\n        "H:SANIL:Core",\n    )\n'''


def main() -> int:
    text = PATH.read_text(encoding="utf-8")
    if OLD not in text:
        raise RuntimeError("Sanil linkage regression target not found")
    PATH.write_text(text.replace(OLD, NEW, 1), encoding="utf-8")
    print("Sanil linkage regression now includes the UHV capacity hypothesis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
