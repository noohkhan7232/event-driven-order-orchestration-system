"""Enforce production statement coverage from pytest-cov's JSON report."""

import argparse
import json
from pathlib import Path

MODULES = (
    "app/providers/manual.py",
    "app/services/shipping.py",
    "app/api/v1/endpoints/shipments.py",
    "app/core/ssrf.py",
    "app/services/outbound_webhooks.py",
    "app/services/webhook_delivery.py",
    "app/api/v1/endpoints/webhook_endpoints.py",
    "app/schemas/webhooks.py",
    "app/notifications/channels.py",
    "app/notifications/service.py",
    "app/api/v1/endpoints/notification_settings.py",
    "app/services/tracking.py",
    "app/api/v1/endpoints/public_tracking.py",
    "app/services/sse.py",
    "app/api/v1/endpoints/streams.py",
    "app/schemas/failed_jobs.py",
    "app/api/v1/endpoints/failed_jobs.py",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    document = json.loads(args.report.read_text(encoding="utf-8"))
    files = {name.replace("\\", "/"): row for name, row in document["files"].items()}
    total = document["totals"]["percent_covered"]
    failures = total < 90
    print(f"TOTAL production: {total:.2f}% (required >=90%)")
    for name in MODULES:
        summary = files.get(name, {}).get("summary")
        if summary is None:
            failures = True
            print(f"FAIL {name}: missing coverage")
        else:
            percent = summary["percent_covered"]
            failures |= percent < 90
            print(f"{'PASS' if percent >= 90 else 'FAIL'} {name}: {percent:.2f}%")
    return int(failures)


if __name__ == "__main__":
    raise SystemExit(main())
