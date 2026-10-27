"""Persist the shared JSON output format used by both offset estimators."""

import json


def write_report(report, source):
    """Save a report next to its input and print the same JSON to stdout."""
    output = source.with_suffix(".offset.json")
    report["offset_file"] = str(output)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    output.write_text(text + "\n", encoding="utf-8")
    print(text)
