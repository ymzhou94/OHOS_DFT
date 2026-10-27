"""Convert text ftrace event timestamps using consistent realtime_ts sync points."""

import argparse
import json
import re
from decimal import Decimal
from pathlib import Path

from get_trace_epoch_offset import calculate_offset


EVENT = re.compile(r"^(\s*[^\r\n]*?\[\d+\]\s+\S+\s+)(\d+\.\d+)(?=:\s)", re.MULTILINE)
EPOCH_HEADER = "# trace_to_epoch: timestamps=epoch; unit=seconds"


def convert_text(text):
    """Shift event headers to epoch time using the shared marker estimator."""
    if text.startswith(EPOCH_HEADER):
        raise ValueError("Input already contains epoch timestamps from trace_to_epoch.")

    report = calculate_offset(text.splitlines())
    offset = Decimal(report["epoch_minus_kernel_seconds"])

    def replace_timestamp(match):
        return match[1] + format(Decimal(match[2]) + offset, "f")

    converted, count = EVENT.subn(replace_timestamp, text)
    header = (
        f"{EPOCH_HEADER}\n"
        f"# epoch_minus_kernel_seconds={offset}; sync_points={report['sync_point_count']}\n"
        "# Sync resolution is milliseconds; absolute error bound not measured.\n"
    )
    return header + converted, {**report, "events_converted": count}


def main():
    """Create a derived epoch trace while preserving the original input."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path, help="Original text ftrace file")
    parser.add_argument("output", type=Path, help="New file; must not already exist")
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        parser.error("Input and output must be different files.")
    converted, report = convert_text(args.input.read_text(encoding="utf-8-sig"))
    with args.output.open("x", encoding="utf-8", newline="") as output:
        output.write(converted)
    report["output"] = str(args.output.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
