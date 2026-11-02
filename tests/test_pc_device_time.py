"""Check clock bounds and the interactive hdc exchange without a device."""

import unittest
from unittest.mock import call, patch

from test_time import collect_samples, offset_bounds, pair_samples


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
        with self.assertRaisesRegex(ValueError, "No common clock offset"):
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
            with self.subTest(bad=bad), self.assertRaisesRegex(ValueError, "mismatch"):
                pair_samples([bad], upstream, downstream)

    def test_incomplete_relay_frame(self):
        """A truncated length-prefixed reply cannot produce a clock result."""
        with self.assertRaisesRegex(ValueError, "Incomplete HDC frame"):
            pair_samples([], [], [(10, b"\x00\x00\x00\x05ab")])

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
