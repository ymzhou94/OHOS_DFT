"""Check clock bounds and the interactive hdc exchange without a device."""

import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import call, patch

from test_time import collect_samples, main, offset_bounds, pair_samples


SNAPSHOT = ("CLOCK_REALTIME : 1700000010.500000123\nCLOCK_MONOTONIC : 1.0\n"
            "CLOCK_BOOTTIME : 2.500000000")
BOOT_EPOCH_NS = 1_700_000_008_000_000_123


class PcDeviceTimeTests(unittest.TestCase):
    """Treat device timestamps as truncated microseconds and PC timestamps as nanoseconds."""

    def test_intersection(self):
        """Opposite ends can come from different samples, including the last truncated ns."""
        epoch = 1_793_195_871_311_095_000
        samples = [(epoch + 900, epoch, epoch + 1100),
                   (epoch + 1700, epoch + 1000, epoch + 1850)]
        self.assertEqual(offset_bounds(samples), (-850, 99))

    def test_disjoint_samples(self):
        """A clock step can make the intersection empty."""
        with self.assertRaisesRegex(ValueError, "时差区间无交集"):
            offset_bounds([(1000, 1000, 1100), (2000, 4000, 2100)])

    def test_relay_pairing(self):
        """TCP reads can split frame headers or combine several HDC frames."""
        def frame(payload):
            return len(payload).to_bytes(4, "big") + payload

        send = frame(b"\r")
        setup = frame(b"HDC[1.000000]")
        reply = frame(b"\r\nHDC[2.000") + frame(b"001]")
        upstream = [(11, send[:2]), (12, send[2:])]
        downstream = [(8, setup), (15, reply[:-2]), (16, reply[-2:])]
        device = 2_000_001_000
        self.assertEqual(pair_samples([(10, device, 20)], upstream, downstream),
                         [(12, device, 16)])
        for bad in [(13, device, 20), (10, device + 1000, 20), (10, device, 14)]:
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "收发顺序不符"):
                pair_samples([bad], upstream, downstream)

    def test_incomplete_relay_frame(self):
        """A truncated length-prefixed reply cannot produce a clock result."""
        with self.assertRaisesRegex(ValueError, "HDC 数据帧不完整"):
            pair_samples([], [], [(10, b"\x00\x00\x00\x05ab")])

    @patch("sys.argv", ["test_time.py"])
    @patch("test_time.device_shell", return_value=SNAPSHOT)
    def test_quick_snapshot(self, shell):
        """The device snapshot is already visible when the first probe starts."""
        with redirect_stdout(StringIO()) as output:
            def collect():
                self.assertIn(SNAPSHOT, output.getvalue())
                return [(4000, 2000, 5000)]

            with patch("test_time.collect_samples", side_effect=collect):
                main()
        shell.assert_called_once_with("timestamps")
        self.assertEqual(output.getvalue().count(SNAPSHOT), 1)
        self.assertIn("设备时间减 PC 时间的区间：[-3000, -1001] 纳秒", output.getvalue())
        self.assertIn(f"boot_epoch = {BOOT_EPOCH_NS} 纳秒", output.getvalue())
        self.assertIn(f"boot_epoch_pc = {BOOT_EPOCH_NS + 2000} 纳秒", output.getvalue())
        self.assertIn(f"[{BOOT_EPOCH_NS + 1001}, {BOOT_EPOCH_NS + 3000}]", output.getvalue())

    @patch("test_time.device_shell", return_value=SNAPSHOT)
    def test_trace_snapshot(self, shell):
        """Trace mode displays and saves the same snapshot used for its boot anchor."""
        def capture(directory, _tag):
            self.assertIn(SNAPSHOT, output.getvalue())
            (directory / "boot.ftrace").touch()
            return [(1000, 5000, 4000)], [(2000, 5000, 3500)]

        with TemporaryDirectory() as temp, redirect_stdout(StringIO()) as output:
            run = Path(temp) / "trace"
            with patch("sys.argv", ["test_time.py", "--trace", str(run)]), \
                    patch("test_time.capture_trace", side_effect=capture):
                main()
            self.assertEqual((run / "timestamps.txt").read_text(encoding="utf-8"), SNAPSHOT + "\n")
            result = json.loads((run / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(result["boot_epoch_ns"], BOOT_EPOCH_NS)
            self.assertEqual(result["boot_epoch_pc_ns"], BOOT_EPOCH_NS - 2750)
            self.assertEqual(result["boot_epoch_pc_bounds_ns"],
                             [BOOT_EPOCH_NS - 3999, BOOT_EPOCH_NS - 1500])
        shell.assert_called_once_with("timestamps")
        self.assertEqual(output.getvalue().count(SNAPSHOT), 1)
        self.assertIn("修正后时差区间：[1500, 3999] 纳秒", output.getvalue())
        self.assertIn(f"boot_epoch_pc = {BOOT_EPOCH_NS - 2750} 纳秒", output.getvalue())

    @patch("test_time.SAMPLE_COUNT", 2)
    @patch("test_time.time.time_ns", side_effect=range(1, 9))
    @patch("test_time.subprocess.Popen")
    def test_prompt_sampling(self, spawn, _clock):
        """Skip setup and timestamp the chunk that finishes each fresh response."""
        shell = spawn.return_value.__enter__.return_value
        shell.stdout.read.side_effect = [
            b"$\x1b[1C", b"PS1='HDC[$EPOCHREALTIME]'\r\n", b"HDC[100.000000]",
            b"HDC[1793195871.311", b"095]", b"HDC[1793195872.000003]",
        ]
        self.assertEqual(collect_samples(), [
            (4, 1_793_195_871_311_095_000, 6), (7, 1_793_195_872_000_003_000, 8)])
        self.assertEqual(shell.stdin.write.call_args_list, [
            call(b"PS1='HDC[$EPOCHREALTIME]'\r"), call(b"\r"), call(b"\r"), call(b"exit\r")])
        spawn.assert_called_once()

    @patch("test_time.subprocess.Popen")
    def test_missing_clock_reports_device_output(self, spawn):
        """Keep the unsupported shell's actual response in diagnostics."""
        shell = spawn.return_value.__enter__.return_value
        shell.stdout.read.side_effect = [b"# ", b"HDC[]", b""]
        shell.poll.return_value = None
        with self.assertRaisesRegex(RuntimeError, r"HDC\[\]"):
            collect_samples()
        shell.kill.assert_called_once()
