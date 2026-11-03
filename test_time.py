"""通过 HDC 测量设备与 PC 的时间差，正值表示设备时钟领先。

Windows 上请使用 CPython 3.13 或更高版本，以获取高分辨率的 PC 时间戳。
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
    raise RuntimeError(f"HDC 连接已关闭，未读到 {pattern.pattern!r}。设备输出：{output!r}")


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
        raise ValueError(f"时差区间无交集（下界 {lower} ns，上界 {upper} ns），请检查采样期间是否发生校时。")
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
                raise TimeoutError("等待本地 HDC 连接数据超时。")
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
        raise ValueError(f"HDC 数据帧不完整，剩余 {len(buf)} 字节。")


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
        raise ValueError(f"采样次数与收发数量不符：采样 {len(samples)} 次，请求 {len(sends)} 次，回复 {len(prompts)} 次。")
    pairs = []
    for raw, sent, stamp in zip(samples, sends, prompts):
        received = arrivals[stamp.end() - 1]
        device = prompt_ns(stamp)
        if device != raw[1] or not raw[0] <= sent <= received <= raw[2]:
            raise ValueError(f"第 {len(pairs) + 1} 次采样的 HDC 时间戳或收发顺序不符。")
        pairs.append((sent, device, received))
    return pairs


def device_shell(command):
    """Require a device-side success marker, since the HDC client can exit zero on failure."""
    result = subprocess.check_output(["hdc", "shell", command + " && echo CLOCK_PROBE_OK"],
                                     stderr=subprocess.STDOUT, text=True, timeout=30)
    output, _, status = result.rstrip().rpartition("\n")
    if status != "CLOCK_PROBE_OK":
        raise RuntimeError(f"设备命令执行失败：{command}\n{result}")
    return output


def boot_anchor(directory=None):
    """Read the device boot-to-epoch offset from one clock snapshot."""
    text = device_shell("timestamps")
    print(f"设备时钟快照（hdc shell timestamps）\n{text}", flush=True)
    if directory is not None:
        (directory / "timestamps.txt").write_text(text + "\n", encoding="utf-8")
    clocks = []
    for name in ("CLOCK_REALTIME", "CLOCK_BOOTTIME"):
        values = re.findall(rf"^{name}[ \t]*:[ \t]*(\d+\.\d{{1,9}})[ \t]*$", text, re.M)
        if len(values) != 1:
            raise ValueError(f"timestamps 输出中应有一条 {name} 记录，实际匹配到 {values!r}")
        clocks.append(int(Decimal(values[0]) * 10**9))
    return clocks[0] - clocks[1]


def report_boot_epochs(boot_epoch, lower, upper):
    """Convert device-minus-PC bounds to the PC epoch for device boot time zero."""
    pc_lower, pc_upper = boot_epoch - upper, boot_epoch - lower
    pc_midpoint = (pc_lower + pc_upper) // 2
    print(f"boot_epoch = {boot_epoch} 纳秒（设备时间基准）")
    print(f"boot_epoch_pc = {pc_midpoint} 纳秒（PC 时间基准）")
    print(f"按本次时差换算的 boot_epoch_pc 区间：[{pc_lower}, {pc_upper}] 纳秒")
    return {"boot_epoch_ns": boot_epoch, "boot_epoch_pc_ns": pc_midpoint,
            "boot_epoch_pc_bounds_ns": [pc_lower, pc_upper]}


def capture_trace(directory, tag):
    """Capture the relayed probes and retrieve the associated boot-clock trace."""
    remote = f"/data/local/tmp/{tag}.ftrace"
    begin = device_shell("hitrace --trace_begin -b 65536 --trace_clock boot app sched hdcd usb")
    if "OpenRecording done" not in begin:
        raise RuntimeError(f"HiTrace 未确认开始采集。设备输出：{begin}")
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
        raise RuntimeError(f"未能从设备取回 {remote}。HDC 输出：{transfer}")
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
    print(f"设备时间减 PC 时间（中点 ± 半宽）：{(lower + upper) / 2e6:+.3f} ± {(upper - lower) / 2e6:.3f} 毫秒")
    print(f"区间半宽：修正前 {(raw_upper - raw_lower) / 2000:.1f} 微秒，修正后 {(upper - lower) / 2000:.1f} 微秒")
    print(f"修正后时差区间：[{lower}, {upper}] 纳秒")
    result.update(report_boot_epochs(anchor, lower, upper))
    (directory / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(f"匹配到 HiTrace 调度记录的采样：{matched}/{len(samples)}")
    print(f"输出目录：{directory.resolve()}")


def main():
    """Report a quick offset, or capture and correct the same probes with HiTrace."""
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    options = parser.add_argument_group("选项")
    options.add_argument("-h", "--help", action="help", help="显示帮助并退出")
    options.add_argument("--trace", type=Path, metavar="新目录",
                         help="采集 HiTrace，并将修正后的时差及采样记录保存到新目录")
    args = parser.parse_args()
    if args.trace is not None:
        traced_measurement(args.trace)
        return
    anchor = boot_anchor()
    samples = collect_samples()
    lower, upper = offset_bounds(samples)
    before, device, after = min(samples, key=lambda row: row[2] - row[0])
    print(f"{len(samples)} 次采样中最短往返耗时：{(after - before) / 1e6:.3f} 毫秒")
    print(f"该次采样（纳秒）：PC 发送前 {before}，设备读钟 {device}，PC 收到提示符 {after}")
    print(f"设备时间减 PC 时间的区间：[{lower}, {upper}] 纳秒")
    print(f"时差（中点 ± 半宽）：{(lower + upper) / 2e6:+.3f} ± {(upper - lower) / 2e6:.3f} 毫秒")
    report_boot_epochs(anchor, lower, upper)


if __name__ == "__main__":
    main()
