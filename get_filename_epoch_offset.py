"""Intersect filename-derived epoch-minus-boot intervals (default device timezone UTC+08:00).

With no manifest, collect 20 one-second raw traces via hdc and retain their names.
Binary trace contents are not needed. A manifest may contain filenames or hitrace output.
"""

import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from hdc_capture import run_hdc
from offset_report import write_report


NS_PER_SECOND = 1_000_000_000
SAMPLES = 20
UNIX_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
FILENAME = re.compile(
    r"(?<!\w)((?:record_trace|record|trace)_(\d{14})@(\d+)-(\d{1,9})\.sys)(?!\w)"
)


def parse_utc_offset(value):
    """Parse an explicit device UTC offset without using the host timezone."""
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value)
    if not match or int(match[2]) > 23 or int(match[3]) > 59:
        raise ValueError("Device UTC offset must use +/-HH:MM, for example +08:00.")
    minutes = (int(match[2]) * 60 + int(match[3])) * (1 if match[1] == "+" else -1)
    return timezone(timedelta(minutes=minutes))


def calculate_filename_offset(text, utc_offset="+08:00"):
    """Intersect filename quantization intervals and report their midpoint."""
    device_timezone = parse_utc_offset(utc_offset)
    points = []
    seen = set()
    for match in FILENAME.finditer(text):
        name = match[1]
        if name in seen:
            continue
        seen.add(name)
        local = datetime.strptime(match[2], "%Y%m%d%H%M%S").replace(tzinfo=device_timezone)
        epoch_seconds = (local - UNIX_EPOCH) // timedelta(seconds=1)
        boot_ns = int(match[3]) * NS_PER_SECOND + int(match[4])
        lower = epoch_seconds * NS_PER_SECOND - boot_ns
        points.append({
            "filename": name,
            "epoch_floor_seconds": epoch_seconds,
            "boot_nanoseconds": str(boot_ns),
            "offset_lower_inclusive_nanoseconds": str(lower),
            "offset_upper_exclusive_nanoseconds": str(lower + NS_PER_SECOND),
        })
    if not points:
        raise ValueError("No hitrace filename with local time and boot nanoseconds found.")
    lower_point = max(points, key=lambda p: int(p["offset_lower_inclusive_nanoseconds"]))
    upper_point = min(points, key=lambda p: int(p["offset_upper_exclusive_nanoseconds"]))
    lower = int(lower_point["offset_lower_inclusive_nanoseconds"])
    upper = int(upper_point["offset_upper_exclusive_nanoseconds"])
    if lower >= upper:
        raise ValueError(
            "Filename offset intervals have no intersection. Check device boot, timezone, "
            f"clock adjustments and filename sampling timing. Samples: {json.dumps(points)}"
        )
    estimate = (lower + upper) // 2
    return {
        "definition": "offset = epoch - boot",
        "device_timezone": utc_offset,
        "filename_count": len(points),
        "epoch_minus_kernel_seconds": format(Decimal(estimate) / NS_PER_SECOND, ".9f"),
        "epoch_minus_kernel_nanoseconds": str(estimate),
        "offset_selection": "midpoint of the intersection of all filename intervals",
        "offset_lower_inclusive_nanoseconds": str(lower),
        "offset_upper_exclusive_nanoseconds": str(upper),
        "interval_width_nanoseconds": upper - lower,
        "quantization_uncertainty_nanoseconds": (upper - lower + 1) // 2,
        "uncertainty_scope": (
            "filename quantization only; assumes constant offset and "
            "simultaneous epoch/boot filename sampling"
        ),
        "lower_limiting_filename": lower_point["filename"],
        "upper_limiting_filename": upper_point["filename"],
        "epoch_to_kernel": "boot_ns = epoch_ns - int(epoch_minus_kernel_nanoseconds)",
        "sync_points": points,
    }


def collect_filenames():
    """Capture raw traces and retain their filenames and diagnostic output."""
    directory = Path("filename_epoch_sync_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f")).resolve()
    directory.mkdir()
    manifest = directory / "filenames.txt"
    with (directory / "capture.log").open("w", encoding="utf-8") as log, \
            manifest.open("w", encoding="utf-8") as names:
        for index in range(SAMPLES):
            log.write(f"Sample {index + 1}/{SAMPLES}\n")
            log.flush()
            result = run_hdc(
                ["shell", "hitrace -t 1 -b 204800 app sched --trace_clock boot --raw"], log
            )
            matches = list(FILENAME.finditer(result))
            if len(matches) != 1:
                raise RuntimeError(
                    f"Expected one filename from successful hitrace: {result}; log: {log.name}"
                )
            names.write(matches[0][1] + "\n")
            names.flush()
    return manifest


def main():
    """Select live or offline filename analysis and emit the offset report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", nargs="?", type=Path,
                        help="Text containing filenames; omit to collect 20 raw traces")
    parser.add_argument(
        "--utc-offset", default="+08:00", help="Device timezone, +/-HH:MM (default +08:00)"
    )
    args = parser.parse_args()
    parse_utc_offset(args.utc_offset)
    captured = args.manifest is None
    manifest = collect_filenames() if captured else args.manifest.resolve()
    report = calculate_filename_offset(manifest.read_text(encoding="utf-8-sig"), args.utc_offset)
    if captured and report["filename_count"] != SAMPLES:
        raise ValueError(f"Expected {SAMPLES} distinct filenames; inspect {manifest}.")
    report["manifest"] = str(manifest)
    write_report(report, manifest)


if __name__ == "__main__":
    main()
