"""Print epoch-minus-kernel offset as JSON. With no input, collect 20 boot sync points via hdc."""

import argparse
import json
import re
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from statistics import median

from hdc_capture import run_hdc
from offset_report import write_report


SYNC = re.compile(
    r"^[^\r\n]*\[\d+\]\s+\S+\s+(\d+\.\d{1,9}):\s+"
    r"tracing_mark_write:\s+trace_event_clock_sync:\s+realtime_ts=(\d+)\s*$"
)
NS_PER_SECOND = 1_000_000_000
MAX_SPREAD_NS = 1_000_000  # realtime_ts resolution is 1ms; this is a consistency check.
SYNC_SAMPLES = 20
SAMPLE_INTERVAL_SECONDS = 0.05


def calculate_offset(lines):
    """Validate sync points and estimate a fixed epoch-minus-kernel offset."""
    points = []
    for number, line in enumerate(lines, 1):
        if line.startswith("# trace_to_epoch: timestamps=epoch"):
            raise ValueError("Input already has epoch timestamps; use the original kernel trace.")
        match = SYNC.match(line)
        if "trace_event_clock_sync:" in line and "realtime_ts=" in line and not match:
            raise ValueError(f"Malformed realtime_ts sync point at line {number}: {line.rstrip()}")
        if match:
            kernel_ns = int(Decimal(match[1]) * NS_PER_SECOND)
            epoch_ms = int(match[2])
            points.append({
                "line": number,
                "kernel_seconds": match[1],
                "epoch_milliseconds": epoch_ms,
                "epoch_minus_kernel_nanoseconds": str(epoch_ms * 1_000_000 - kernel_ns),
            })
    if not points:
        raise ValueError("No realtime_ts sync point found in the original text ftrace.")

    offsets = [int(point["epoch_minus_kernel_nanoseconds"]) for point in points]
    spread = max(offsets) - min(offsets)
    if spread > MAX_SPREAD_NS:
        raise ValueError(
            f"Offset spread is {spread}ns, exceeding the 1ms consistency limit. "
            f"A fixed offset was not accepted. Sync points: {json.dumps(points)}"
        )
    offset = max(offsets)
    return {
        "definition": "offset = epoch - kernel",
        "epoch_minus_kernel_seconds": format(Decimal(offset) / NS_PER_SECOND, ".9f"),
        "epoch_minus_kernel_nanoseconds": str(offset),
        "epoch_to_kernel": "kernel_ns = epoch_ns - int(epoch_minus_kernel_nanoseconds)",
        "kernel_to_epoch": "epoch_ns = kernel_ns + int(epoch_minus_kernel_nanoseconds)",
        "offset_selection": (
            "maximum observed offset; assumes truncation and "
            "marker-write latency bias samples downward"
        ),
        "sync_point_count": len(points),
        "sample_span_seconds": format(
            Decimal(points[-1]["kernel_seconds"]) - Decimal(points[0]["kernel_seconds"]), "f"),
        "offset_min_nanoseconds": str(min(offsets)),
        "offset_max_nanoseconds": str(offset),
        "offset_median_nanoseconds": format(median([Decimal(value) for value in offsets]), "f"),
        "offset_spread_nanoseconds": spread,
        "consistency_limit_nanoseconds": MAX_SPREAD_NS,
        "consistency": (
            "multiple_points_within_1ms" if len(points) > 1 else "single_point_unverified"
        ),
        "sync_resolution": "milliseconds; absolute error bound not measured",
        "scope": (
            "same device boot and kernel clock, with no epoch clock adjustment; "
            "mono also requires no suspend"
        ),
        "sync_points": points,
    }


def capture_boot_trace():
    """Collect boot-clock markers and preserve the trace and command logs."""
    directory = Path("trace_epoch_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")).resolve()
    directory.mkdir()
    remote = "/data/local/tmp/" + directory.name
    trace = directory / "boot.ftrace"
    with (directory / "capture.log").open("w", encoding="utf-8") as log:
        active = False
        try:
            run_hdc(["shell", "hitrace --trace_begin -b 204800 app sched --trace_clock boot"], log)
            active = True
            for _ in range(SYNC_SAMPLES - 1):
                run_hdc(["shell", f"hitrace --trace_dump -o {remote}_sample.ftrace"], log)
                time.sleep(SAMPLE_INTERVAL_SECONDS)
            run_hdc(["shell", f"hitrace --trace_finish -o {remote}.ftrace"], log)
            active = False
        finally:
            if active:
                run_hdc(["shell", "hitrace --trace_finish_nodump"], log)
        run_hdc(["file", "recv", remote + ".ftrace", str(trace)], log)
    return trace


def main():
    """Select live or offline trace calibration and emit the offset report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", nargs="?", type=Path,
                        help="Existing original text ftrace; omit to capture 20 sync points")
    args = parser.parse_args()
    captured = args.trace is None
    trace = capture_boot_trace() if captured else args.trace.resolve()
    with trace.open(encoding="utf-8-sig") as source:
        report = calculate_offset(source)
    if captured and report["sync_point_count"] != SYNC_SAMPLES:
        raise ValueError(
            f"Expected {SYNC_SAMPLES} sync points, found {report['sync_point_count']}; "
            f"inspect {trace} and {trace.parent / 'capture.log'}."
        )
    report["trace_file"] = str(trace)
    report["kernel_clock"] = "boot" if captured else "as recorded in the input; not inferred"
    write_report(report, trace)


if __name__ == "__main__":
    main()
