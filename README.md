# OHOS_DFT

[English](README.md) | [简体中文](README.zh-CN.md)

Estimate the offset between an OHOS/HarmonyOS hitrace kernel clock and Unix epoch time, then align another timestamped dataset with the trace. Uses Python's standard library; no third-party packages.

```text
offset = epoch - kernel
kernel_ns = epoch_ns - offset_ns
epoch_ns = kernel_ns + offset_ns
```

The selected JSON offset, `epoch_minus_kernel_nanoseconds`, is a decimal integer string. Parse it with `int`, not floating-point arithmetic. Median statistics can contain fractional nanoseconds and use decimal strings. Nanosecond representation does not imply nanosecond accuracy.

## Choose a method

| Method | Input | Estimate and limitations |
| --- | --- | --- |
| Multiple `realtime_ts` markers (recommended) | Original text ftrace | Maximum observed offset under a downward-error model; includes all samples, median and spread. Markers contain epoch milliseconds. Absolute accuracy is not bounded. |
| Filename interval intersection (fewest data dependencies) | Filenames or command output, plus the device UTC offset | Intersects one-second offset intervals; reports their midpoint, bounds and quantization uncertainty. Does not read binary traces or require hilog. |

These are independent, explicit methods. A missing marker never causes an automatic switch to filename inference. Neither method silently drops inconsistent samples.

## Requirements

- Python 3.9+; tested with Python 3.14 on Windows/PowerShell.
- Offline analysis requires only the source files and the scripts in this repository.
- Online sampling requires `hdc` on `PATH`, one connected device, developer/debugging access and permission to use `hitrace`.
- Run one sampler at a time. Online sampling changes shared device tracing state and uses a 204800 KB buffer.

The live paths explicitly request the `boot` clock. It includes suspend time. Use an offset only for the same device boot, kernel clock and epoch reference. Recalibrate after a reboot or epoch clock adjustment. A `mono`-to-epoch offset also changes across suspend. For offline traces, the scripts do not infer or verify the selected clock.

## Quick start without a device

The files in `examples/` are synthetic fixtures.

```sh
python get_trace_epoch_offset.py examples/boot.ftrace
python get_filename_epoch_offset.py examples/filenames.txt --utc-offset=+08:00
python trace_to_epoch.py examples/boot.ftrace converted_epoch.ftrace
python -m unittest discover -s tests -t . -v
```

The first two commands print JSON and save `examples/boot.offset.json` and `examples/filenames.offset.json`. The synthetic examples produce offsets of `1792781198.324412000` seconds and `1735689500.225000000` seconds respectively; they represent different example timelines.

The converter creates a new file and refuses to overwrite an existing output or convert its own output twice. It changes event header timestamps and adds calibration comments; timestamps embedded in event payloads remain unchanged. `converted_epoch.ftrace` is a derived analysis file; viewer support for epoch timestamps varies, so retain the original trace.

## Sample a connected device

### Recommended: multiple trace synchronization markers

```sh
python get_trace_epoch_offset.py
```

The script:

1. Starts a trace with `--trace_clock boot` and categories `app sched`.
2. Calls `--trace_dump` 19 times, waiting 50 ms after each call.
3. Calls `--trace_finish` for the twentieth marker and retrieves the final text trace.
4. Computes all offsets, validates their spread, and prints/saves the result.

Commands, dumps and transfer add overhead: 20 markers are not a one-second capture. Results are stored under a timestamped `trace_epoch_sync_*/` directory, including `capture.log`, `boot.ftrace` and `boot.offset.json`.

To analyze an existing original text trace instead:

```sh
python get_trace_epoch_offset.py path/to/original.ftrace
```

At least one well-formed `realtime_ts` marker is required. A single marker is labeled `single_point_unverified`. Multiple markers must have an offset spread of at most 1 ms. This threshold is a consistency check, not a promised error bound; scheduling delay can also cause rejection. On failure, inspect the reported points and capture log.

### Fewest data dependencies: filenames

```sh
python get_filename_epoch_offset.py --utc-offset=+08:00
```

This performs 20 separate one-second raw captures and saves their filenames and command output under `filename_epoch_sync_*/`. Each capture has additional setup/dump overhead. Binary traces remain on the device; the script does not transfer or delete them. Ensure adequate device storage and manage those files separately.

For existing filenames, place one or more names, full paths, or hitrace console output in a text file:

```sh
python get_filename_epoch_offset.py filenames.txt --utc-offset=+08:00
```

The UTC offset is the **device's local offset at capture time**, not the host timezone. The explicit default is `+08:00`; use `--utc-offset=-05:00` for a negative offset. A single manifest must use one UTC offset. All names must belong to the same boot with a constant epoch-to-boot offset.

Supported prefixes are `trace_`, `record_` and `record_trace_`. The part after `-` is an integer nanosecond field, not a decimal fraction: `@334949-18089377` means `334949.018089377` seconds. Duplicate filenames do not add information.

## Use the offset with another dataset

After calibration, read its JSON once before processing the other dataset:

```python
import json
from decimal import Decimal

with open("examples/boot.offset.json", encoding="utf-8") as source:
    calibration = json.load(source)

offset_ns = int(calibration["epoch_minus_kernel_nanoseconds"])

# Replace this string with each record's epoch timestamp in seconds.
epoch_seconds = "1793113965.749000000"
epoch_ns = int(Decimal(epoch_seconds) * 1_000_000_000)
kernel_ns = epoch_ns - offset_ns
```

If the input is already integer epoch nanoseconds, subtract the offset directly. For epoch milliseconds, multiply by `1_000_000` first. A dataset timestamped on another host must share the device's epoch time reference; these scripts do not measure host/device clock skew.

For filename calibration, keep `offset_lower_inclusive_nanoseconds`, `offset_upper_exclusive_nanoseconds` and `quantization_uncertainty_nanoseconds` alongside the midpoint. They express filename quantization uncertainty under the stated model, not a measured absolute error bound.

## How the estimates work

### Marker model

For a marker with trace timestamp `K_i` seconds and `realtime_ts=R_i` milliseconds:

```text
O_i = R_i / 1000 - K_i
O_estimate = max(O_i)
```

The public hitrace implementation reads `CLOCK_REALTIME`, truncates it to milliseconds, and then writes `trace_marker`. If the offset is constant and truncation/write delay are the only errors:

```text
measured offset = true offset - truncation - read-to-marker delay
```

Taking the maximum favors the least downward-biased observation. This is a conditional estimator, not proof of the true offset. More samples can improve repeatability without establishing absolute accuracy. All arithmetic uses integers or `Decimal`; even-sample medians avoid floating-point loss.

### Filename model

Let `W_i` be the filename's local whole second converted to epoch, and `B_i` its boot timestamp:

```text
O belongs to [W_i - B_i, W_i + 1 second - B_i)
lower = max(W_i - B_i)
upper = min(W_i + 1 second - B_i)
estimate = midpoint of [lower, upper)
```

The midpoint minimizes the maximum quantization error within that interval. More filenames narrow the interval only when their subsecond phases add information. An empty intersection is an error. The interval assumes the filename's wall and boot values describe the same instant closely enough; their sampling delay is not bounded by this tool.

## Observations from device experiments

These are observations from one device/boot, not general accuracy guarantees:

- Two independent batches of 20 marker samples had maximum-offset estimates differing by 119 microseconds.
- Twenty filenames reduced a one-second interval to 47.076719 ms wide.
- A 1000-record hilog comparison found whole-second jumps in the displayed `monotonic` offset and a MUSL record in a different seconds domain, including within the same PID. The displayed fraction reuses realtime nanoseconds. Therefore, this tool does not treat all hilog `monotonic` output as a uniform boot or mono clock.
- A 107.187 microsecond-wide constraint inferred from hilog integer-second boundaries depends on an unverified producer-clock model. It is not used as the production calibration method.

## Diagnostics and scope

- Device commands include an explicit remote exit-status marker. An `hdc` host exit code of zero alone is not accepted as device-command success.
- Missing/malformed markers, marker spread above 1 ms, incomplete live sampling, invalid timezones and incompatible filename intervals raise errors. Source data and command logs remain available.
- The text converter reads text ftrace only; it does not decode binary `.sys` or `.htrace` files.
- Live collection uses the device's hitrace behavior; supported clocks, categories and permissions vary by device. There is no automatic clock or format substitution.
- Raw device captures, diagnostic logs, generated offset reports and Python caches are excluded from Git. Keep captured filenames and reports locally when reproducing a result.

## Tests

The unit tests are in [`tests/`](tests/README.md). They cover marker offset estimation, filename interval intersection, hdc failure diagnostics and trace conversion. All inputs are synthetic or mocked, so no connected device is needed. Run a single module with `python -m unittest tests.test_hdc_capture -v`. GitHub Actions runs the suite and Pylint on Python 3.8, 3.9 and 3.10.

## Authors

- [ymzhou94](https://github.com/ymzhou94) — project owner and requirements.
- **Codex (OpenAI)** — AI co-author of the implementation, tests and documentation.

## Source references

- [Huawei hitrace documentation](https://developer.huawei.com/consumer/cn/doc/doccenter-capabilities/hitrace)
- [OpenHarmony MarkClockSync: realtime milliseconds and parent_ts](https://github.com/openharmony/hiviewdfx_hitrace/blob/master/utils/common_utils.cpp#L76)
- [OpenHarmony text capture/dump/finish marker calls](https://github.com/openharmony/hiviewdfx_hitrace/blob/master/cmd/hitrace_cmd.cpp#L1740)
- [HiLog timestamp formatting](https://github.com/openharmony/hiviewdfx_hilog/blob/master/frameworks/libhilog/utils/log_print.cpp#L101)
- [MUSL's independent HiLog adapter](https://github.com/openharmony/third_party_musl/blob/master/src/hilog/linux/hilog_adapter.c#L154)
