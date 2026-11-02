"""Compare PC and device epoch time using Python's standard library and hdc.

Use CPython 3.13+ on Windows for high-resolution epoch timestamps.
"""

import argparse
import csv
import json
import os
import re
import secrets
import select
import socket
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Timer


SAMPLE_COUNT = 20
CLOCK = re.compile(rb"HDC\[(\d+)\.(\d{6})\]")


def prompt_ns(stamp):
    """Convert the device's microsecond prompt to integer epoch nanoseconds."""
    return int(stamp[1]) * 1_000_000_000 + int(stamp[2]) * 1_000


def read_prompt(stream, pattern):
    """Timestamp the pipe read that completes the requested prompt."""
    output = b""
    while chunk := stream.read(4096):
        received = time.time_ns()
        output += chunk
        if match := pattern.search(output):
            return received, match
    raise RuntimeError(f"hdc closed before {pattern.pattern!r}. Output: {output!r}")


def collect_samples(address=None, tag=None):
    """Send one carriage return per clock read in a single device shell."""
    client = ["hdc", "-s", address, "shell"] if address else ["hdc", "shell"]
    if sys.platform == "win32":
        command = ["conhost.exe", "--headless", *client]
    elif sys.platform == "darwin":
        command = ["script", "-q", "/dev/null", *client]
    else:
        command = ["script", "-qfc", " ".join(client), "/dev/null"]

    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=0) as shell:
        timeout = Timer(15, shell.kill)
        timeout.start()
        try:
            read_prompt(shell.stdout, re.compile(rb"[$#](?: |\x1b\[\d+C)"))
            setup = f"echo {tag} > /proc/self/comm && ".encode() if tag else b""
            shell.stdin.write(setup + b"PS1='HDC[$EPOCHREALTIME]'\r")
            read_prompt(shell.stdout, CLOCK)  # Discard the setup prompt.
            samples = []
            for index in range(SAMPLE_COUNT):
                # Vary the send phase relative to terminal redraws, outside the timed window.
                time.sleep(index / 1000)
                before = time.time_ns()
                shell.stdin.write(b"\r")
                after, stamp = read_prompt(shell.stdout, CLOCK)
                samples.append((before, prompt_ns(stamp), after))
            shell.stdin.write(b"exit\r")
            shell.wait()
            return samples
        finally:
            timeout.cancel()
            if shell.poll() is None:
                shell.kill()


def offset_bounds(samples):
    """Intersect device-minus-PC intervals, including device microsecond truncation."""
    lower = max(device - after for before, device, after in samples)
    upper = min(device + 999 - before for before, device, after in samples)
    if lower > upper:
        raise ValueError(f"No common clock offset: [{lower}, {upper}] ns. Check clock adjustments.")
    return lower, upper


def relay(listener):
    """Forward one HDC session, timestamping outgoing and incoming TCP chunks."""
    upstream, downstream = [], []
    port = int(os.environ.get("OHOS_HDC_SERVER_PORT", "8710"))
    with listener.accept()[0] as client, socket.create_connection(("127.0.0.1", port), 5) as server:
        for sock in (client, server):
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        while True:
            ready = select.select([client, server], [], [], 15)[0]
            if not ready:
                raise TimeoutError("No traffic on the local HDC connection.")
            for source in ready:
                chunk = source.recv(65536)
                received = time.time_ns()
                if not chunk:
                    return upstream, downstream
                if source is client:
                    sent = time.time_ns()
                    server.sendall(chunk)
                    upstream.append((sent, chunk))
                else:
                    client.sendall(chunk)
                    downstream.append((received, chunk))


def hdc_frames(chunks):
    """Decode length-prefixed HDC frames, retaining the time of their last chunk."""
    buf = bytearray()
    for when, chunk in chunks:
        buf.extend(chunk)
        while len(buf) >= 4:
            end = 4 + int.from_bytes(buf[:4], "big")
            if end > len(buf):
                break
            yield when, bytes(buf[4:end])
            del buf[:end]
    if buf:
        raise ValueError(f"Incomplete HDC frame ({len(buf)} bytes left).")


def pair_samples(samples, upstream, downstream):
    """Pair each CR and device prompt with its own enclosing Python read/write."""
    sends = [when for when, payload in hdc_frames(upstream) if payload == b"\r"]
    data = bytearray()
    arrivals = []
    for when, payload in hdc_frames(downstream):
        data.extend(payload)
        arrivals.extend([when] * len(payload))
    prompts = list(CLOCK.finditer(data))[1:]  # The setup prompt precedes the first probe.
    if len(sends) != len(samples) or len(prompts) != len(samples):
        raise ValueError(f"{len(samples)} probes, {len(sends)} requests, {len(prompts)} replies.")
    pairs = []
    for raw, sent, stamp in zip(samples, sends, prompts):
        received = arrivals[stamp.end() - 1]
        device = prompt_ns(stamp)
        if device != raw[1] or not raw[0] <= sent <= received <= raw[2]:
            raise ValueError(f"HDC timestamp or ordering mismatch for probe {len(pairs) + 1}.")
        pairs.append((sent, device, received))
    return pairs


def device_shell(command):
    """Require a device-side success marker, since the HDC client can exit zero on failure."""
    result = subprocess.check_output(["hdc", "shell", command + " && echo CLOCK_PROBE_OK"],
                                     stderr=subprocess.STDOUT, text=True, timeout=30)
    output, _, status = result.rstrip().rpartition("\n")
    if status != "CLOCK_PROBE_OK":
        raise RuntimeError(f"{command}: {result}")
    return output


def boot_anchor(directory):
    """Read realtime and boottime in one device command, before tracing."""
    text = device_shell("timestamps")
    (directory / "timestamps.txt").write_text(text + "\n", encoding="utf-8")
    clocks = []
    for name in ("CLOCK_REALTIME", "CLOCK_BOOTTIME"):
        values = re.findall(rf"^{name}[ \t]*:[ \t]*(\d+\.\d{{1,9}})[ \t]*$", text, re.M)
        if len(values) != 1:
            raise ValueError(f"Expected one {name} in timestamps.txt, got {values!r}")
        clocks.append(int(Decimal(values[0]) * 10**9))
    return clocks[0] - clocks[1]


def capture_trace(directory, tag):
    """Capture the relayed probes and retrieve the associated boot-clock trace."""
    remote = f"/data/local/tmp/{tag}.ftrace"
    begin = device_shell("hitrace --trace_begin -b 65536 --trace_clock boot app sched hdcd usb")
    if "OpenRecording done" not in begin:
        raise RuntimeError(f"HiTrace did not confirm recording: {begin}")
    try:
        with socket.socket() as listener, ThreadPoolExecutor(max_workers=1) as worker:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            listener.settimeout(15)
            forwarding = worker.submit(relay, listener)
            samples = collect_samples(f"127.0.0.1:{listener.getsockname()[1]}", tag)
            traffic = forwarding.result()
        device_shell(f"hitrace --trace_dump -o {remote}")
    finally:
        device_shell("hitrace --trace_finish_nodump")
    trace = directory / "boot.ftrace"
    transfer = subprocess.check_output(["hdc", "file", "recv", remote, str(trace.resolve())],
                                       stderr=subprocess.STDOUT, text=True, timeout=60)
    if "FileTransfer finish" not in transfer or not trace.is_file() or not trace.stat().st_size:
        raise RuntimeError(f"Could not retrieve {remote}: {transfer}")
    device_shell(f"rm -f {remote}")
    return samples, pair_samples(samples, *traffic)


def running_slices(trace, tag):
    """Pair the tagged shell's switches on each CPU; times are device boot ns."""
    starts, slices = {}, []
    switch = re.compile(r"\[(\d+)\]\s+\S+\s+(\d+\.\d+):\s+sched_switch:")
    with trace.open(encoding="utf-8", errors="replace") as source:
        for line in source:
            if tag not in line or not (match := switch.search(line)):
                continue
            cpu, seconds = match.groups()
            stamp = int(Decimal(seconds) * 10**9)
            if f"prev_comm={tag} " in line and cpu in starts:
                slices.append((starts.pop(cpu), stamp))
            if f"next_comm={tag} " in line:
                starts[cpu] = stamp
    return slices


def write_probes(path, samples, corrected, anchor, slices):
    """Write each probe's observed boundaries and estimated trace position."""
    matched = 0
    with path.open("w", newline="", encoding="utf-8") as dest:
        writer = csv.writer(dest)
        writer.writerow(("seq", "pc_before_ns", "relay_send_ns", "device_realtime_ns",
                         "relay_receive_ns", "pc_after_ns", "boot_ns_est", "switch_in_boot_ns",
                         "switch_out_boot_ns", "pc_before_relay_ns", "pc_after_relay_ns",
                         "corrected_lower_ns", "corrected_upper_ns"))
        for seq, (raw, (sent, device, received)) in enumerate(zip(samples, corrected), 1):
            boot = device - anchor
            hits = [span for span in slices if span[0] <= boot < span[1]]
            matched += len(hits) == 1
            writer.writerow((seq, raw[0], sent, device, received, raw[2], boot,
                             *(hits[0] if len(hits) == 1 else ("", "")), sent - raw[0],
                             raw[2] - received, device - received, device + 999 - sent))
    return matched


def traced_measurement(directory):
    """Save original evidence, per-probe corrections and the clock offset interval."""
    directory.mkdir()
    anchor = boot_anchor(directory)
    tag = "tt_" + secrets.token_hex(4)
    samples, corrected = capture_trace(directory, tag)
    slices = running_slices(directory / "boot.ftrace", tag)
    matched = write_probes(directory / "per-probe.csv", samples, corrected, anchor, slices)
    lower, upper = offset_bounds(corrected)
    raw_lower, raw_upper = offset_bounds(samples)
    result = {"device_minus_pc_bounds_ns": [lower, upper], "raw_bounds_ns": [raw_lower, raw_upper],
              "boot_to_device_epoch_ns": anchor, "shell_tag": tag,
              "samples": len(samples), "matched_trace_slices": matched}
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"Device - PC: {(lower + upper) / 2e6:+.3f} ms +/- {(upper - lower) / 2e6:.3f} ms")
    print(f"Half-width: raw {(raw_upper - raw_lower) / 2000:.1f} us, "
          f"corrected {(upper - lower) / 2000:.1f} us")
    print(f"Interval: [{lower}, {upper}] ns | trace matches: {matched}/{len(samples)}")
    print(directory.resolve())


def main():
    """Report a quick offset, or capture and correct the same probes with HiTrace."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, metavar="NEW_DIR",
                        help="capture HiTrace and save relay-corrected offsets in a new directory")
    args = parser.parse_args()
    if args.trace is not None:
        traced_measurement(args.trace)
        return
    samples = collect_samples()
    lower, upper = offset_bounds(samples)
    before, device, after = min(samples, key=lambda row: row[2] - row[0])
    print(f"Shortest of {len(samples)} requests: {(after - before) / 1e6:.3f} ms")
    print(f"PC before write: {before} ns | device: {device} ns | PC on prompt: {after} ns")
    print(f"Device - PC interval: [{lower}, {upper}] ns")
    print(f"Midpoint: {(lower + upper) / 2e6:+.3f} ms +/- {(upper - lower) / 2e6:.3f} ms")


if __name__ == "__main__":
    main()
