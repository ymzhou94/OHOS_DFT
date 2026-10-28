"""Estimate epoch-minus-boot offset from 20 hdc shell timestamps samples or a saved file."""

import argparse
import json
import re
import time
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from pathlib import Path
from statistics import median

from hdc_capture import run_hdc
from offset_report import write_report


CLOCK_LINE = re.compile(r"(CLOCK_[A-Z_]+)\s*:\s*(\d+\.\d{1,9})")
NS_PER_SECOND = 1_000_000_000
SAMPLE_COUNT = 20
MAX_SPREAD_NS = 1_000_000  # Consistency threshold, not an absolute error bound.


def calculate_offset(text):
    """Validate blank-separated samples and select their rounded median offset."""
    samples = []
    for number, block in enumerate(re.split(r"\n\s*\n", text.strip()), 1):
        clocks = {}
        for line in block.splitlines():
            match = CLOCK_LINE.fullmatch(line.strip())
            if not match:
                raise ValueError(f"Malformed clock line in sample {number}: {line!r}")
            name, seconds = match.groups()
            if name in clocks:
                raise ValueError(
                    f"Duplicate {name} in sample {number}; separate samples with a blank line."
                )
            clocks[name] = str(int(Decimal(seconds) * NS_PER_SECOND))
        for required in ("CLOCK_REALTIME", "CLOCK_BOOTTIME"):
            if required not in clocks:
                raise ValueError(f"Missing {required} in sample {number}.")
        samples.append({
            "clocks_nanoseconds": clocks,
            "epoch_minus_kernel_nanoseconds": str(
                int(clocks["CLOCK_REALTIME"]) - int(clocks["CLOCK_BOOTTIME"])),
        })
    offsets = [int(sample["epoch_minus_kernel_nanoseconds"]) for sample in samples]
    spread = max(offsets) - min(offsets)
    if spread > MAX_SPREAD_NS:
        raise ValueError(
            f"Offset spread is {spread}ns, exceeding the 1ms consistency limit. "
            f"A fixed offset was not accepted. Samples: {json.dumps(samples)}"
        )
    midpoint = median([Decimal(value) for value in offsets])
    selected = int(midpoint.to_integral_value(rounding=ROUND_HALF_EVEN))
    return {
        "method": "hdc shell timestamps",
        "kernel_clock": "boot",
        "definition": "offset = CLOCK_REALTIME - CLOCK_BOOTTIME",
        "epoch_minus_kernel_nanoseconds": str(selected),
        "epoch_minus_kernel_seconds": format(Decimal(selected) / NS_PER_SECOND, ".9f"),
        "offset_selection": "median rounded to nearest nanosecond, ties to even",
        "offset_min_nanoseconds": str(min(offsets)),
        "offset_max_nanoseconds": str(max(offsets)),
        "offset_median_nanoseconds": format(midpoint, "f"),
        "offset_spread_nanoseconds": spread,
        "sample_count": len(samples),
        "consistency_limit_nanoseconds": MAX_SPREAD_NS,
        "consistency": (
            "multiple_points_within_1ms" if len(samples) > 1 else "single_point_unverified"
        ),
        "accuracy": (
            "nanosecond representation; clock read order and separation unverified; "
            "sampling bias uncorrected; spread is not an absolute error bound"
        ),
        "scope": "same device boot and boot-clock trace, with no epoch clock adjustment",
        "samples": samples,
    }


def capture_timestamps():
    """Preserve every device clock reading and the hdc command diagnostics."""
    name = "timestamps_epoch_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    directory = Path(name).resolve()
    directory.mkdir()
    source = directory / "timestamps.txt"
    with (directory / "capture.log").open("w", encoding="utf-8") as log:
        with source.open("w", encoding="utf-8") as output:
            for index in range(SAMPLE_COUNT):
                reading = run_hdc(["shell", "timestamps"], log)
                # Preserve failures for diagnostics, then validate before the next call.
                output.write(reading.strip() + "\n\n")
                output.flush()
                report = calculate_offset(reading)
                if report["sample_count"] != 1:
                    raise ValueError(
                        f"Expected one timestamps sample; inspect {source} and capture.log."
                    )
                if index < SAMPLE_COUNT - 1:
                    time.sleep(0.05)
    return source


def main():
    """Collect or load timestamps output and save a compatible offset JSON."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input", nargs="?", type=Path,
        help="Saved timestamps output, blank-separated samples; omit for live sampling",
    )
    args = parser.parse_args()
    source = capture_timestamps() if args.input is None else args.input.resolve()
    report = calculate_offset(source.read_text(encoding="utf-8-sig"))
    report["source_file"] = str(source)
    write_report(report, source)


if __name__ == "__main__":
    main()
