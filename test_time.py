"""Compare PC and device epoch time using Python's standard library and hdc.

Use CPython 3.13+ on Windows for high-resolution epoch timestamps.
"""

import re
import subprocess
import sys
import time
from threading import Timer


SAMPLE_COUNT = 50
CLOCK = re.compile(rb"HDC\[(\d+)\.(\d{6})\]")


def read_prompt(stream, pattern):
    """Timestamp the pipe read that completes the requested prompt."""
    output = b""
    while chunk := stream.read(4096):
        received = time.time_ns()
        output += chunk
        if match := pattern.search(output):
            return received, match
    raise RuntimeError(f"hdc closed before {pattern.pattern!r}. Output: {output!r}")


def collect_samples():
    """Send one carriage return per clock read in a single device shell."""
    if sys.platform == "win32":
        command = ["conhost.exe", "--headless", "hdc", "shell"]
    elif sys.platform == "darwin":
        command = ["script", "-q", "/dev/null", "hdc", "shell"]
    else:
        command = ["script", "-qfc", "hdc shell", "/dev/null"]

    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, bufsize=0) as shell:
        timeout = Timer(15, shell.kill)
        timeout.start()
        try:
            read_prompt(shell.stdout, re.compile(rb"[$#](?: |\x1b\[\d+C)"))
            shell.stdin.write(b"PS1='HDC[$EPOCHREALTIME]'\r")
            read_prompt(shell.stdout, CLOCK)  # Discard the setup prompt.
            samples = []
            for index in range(SAMPLE_COUNT):
                # Vary the send phase relative to terminal redraws, outside the timed window.
                time.sleep(index / 1000)
                before = time.time_ns()
                shell.stdin.write(b"\r")
                after, stamp = read_prompt(shell.stdout, CLOCK)
                device = int(stamp[1]) * 1_000_000_000 + int(stamp[2]) * 1_000
                samples.append((before, device, after))
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


def main():
    """Report the shortest request and the stable-clock offset interval."""
    samples = collect_samples()
    lower, upper = offset_bounds(samples)
    before, device, after = min(samples, key=lambda row: row[2] - row[0])
    print(f"Shortest of {len(samples)} requests: {(after - before) / 1e6:.3f} ms")
    print(f"PC before write: {before} ns | device: {device} ns | PC on prompt: {after} ns")
    print(f"Device - PC interval: [{lower}, {upper}] ns")
    print(f"Midpoint: {(lower + upper) / 2e6:+.3f} ms +/- {(upper - lower) / 2e6:.3f} ms")


if __name__ == "__main__":
    main()
