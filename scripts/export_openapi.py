"""Export the canonical API contract without connecting to external services."""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs/openapi.json"


def render_openapi() -> str:
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from app.main import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail without writing when the export is stale")
    args = parser.parse_args(argv)
    rendered = render_openapi()
    if args.check:
        if not TARGET.is_file() or TARGET.read_text(encoding="utf-8") != rendered:
            print("OpenAPI export is stale; run python scripts/export_openapi.py")
            return 1
        print("OpenAPI export is fresh")
        return 0
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered, encoding="utf-8", newline="\n")
    print("OpenAPI export updated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
