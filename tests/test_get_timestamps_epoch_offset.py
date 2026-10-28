"""Validate direct clock calibration without a connected device."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from get_timestamps_epoch_offset import calculate_offset, capture_timestamps


def reading(epoch="1793195871.311095937", boot="414671.451414846"):
    """Create a synthetic device response containing distinct boot and mono clocks."""
    return (f"CLOCK_REALTIME : {epoch}\nCLOCK_MONOTONIC : 213812.309200480\n"
            f"CLOCK_BOOTTIME : {boot}\n")


class TimestampsOffsetTests(unittest.TestCase):
    """Check exact arithmetic, strict input diagnostics and logged live collection."""

    def test_boot_clock_and_nanosecond_precision(self):
        """Use BOOTTIME rather than MONOTONIC without losing epoch precision."""
        report = calculate_offset(reading())
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "1792781199859681091")
        self.assertEqual(report["epoch_minus_kernel_seconds"], "1792781199.859681091")
        self.assertEqual(report["kernel_clock"], "boot")
        self.assertEqual(report["consistency"], "single_point_unverified")
        self.assertEqual(report["samples"][0]["clocks_nanoseconds"]["CLOCK_MONOTONIC"],
                         "213812309200480")

    def test_median_rounding_and_all_samples_preserved(self):
        """Retain a fractional median and select an integer nanosecond using ties to even."""
        report = calculate_offset(reading() + "\n" + reading(boot="414671.451414845"))
        self.assertEqual(report["offset_median_nanoseconds"], "1792781199859681091.5")
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "1792781199859681092")
        self.assertEqual(report["offset_spread_nanoseconds"], 1)
        self.assertEqual(len(report["samples"]), 2)

    def test_median_retains_valid_extreme_without_selecting_it(self):
        """A consistent delayed sample stays in the report but does not move the median."""
        report = calculate_offset("\n".join([
            reading(), reading(boot="414671.451414845"), reading(boot="414671.452414845"),
        ]))
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "1792781199859681091")
        self.assertEqual(report["offset_spread_nanoseconds"], 1_000_000)
        self.assertEqual(len(report["samples"]), 3)

    def test_missing_required_clock_is_an_error(self):
        """A missing boot clock cannot silently substitute mono or another method."""
        for text, clock in (("CLOCK_REALTIME : 123.0", "CLOCK_BOOTTIME"),
                            ("CLOCK_BOOTTIME : 1.0", "CLOCK_REALTIME")):
            with self.subTest(clock=clock):
                with self.assertRaisesRegex(ValueError, "Missing " + clock):
                    calculate_offset(text)

    def test_malformed_input_is_not_skipped(self):
        """Reject empty input, command errors and excessive fractional digits."""
        for text in ("", reading() + "permission denied", reading(epoch="1.1234567890")):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, "Malformed|Missing"):
                    calculate_offset(text)

    def test_duplicate_clock_is_an_error(self):
        """Ambiguous sample boundaries and duplicate clock names fail explicitly."""
        with self.assertRaisesRegex(ValueError, "Duplicate CLOCK_REALTIME"):
            calculate_offset(reading() + reading())

    def test_epoch_step_rejects_fixed_offset(self):
        """Do not hide a clock step or a delayed reading by dropping a sample."""
        with self.assertRaisesRegex(ValueError, "fixed offset was not accepted"):
            calculate_offset(reading() + "\n" + reading(epoch="1793195872.311095937"))

    def test_fractional_seconds_and_crlf(self):
        """Support ordinary decimal seconds and Windows newlines exactly."""
        report = calculate_offset("CLOCK_REALTIME : 10.1\r\nCLOCK_BOOTTIME : 1.000000001\r\n")
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "9099999999")

    @patch("get_timestamps_epoch_offset.time.sleep")
    @patch("get_timestamps_epoch_offset.run_hdc", return_value=reading())
    def test_live_collection_preserves_twenty_samples(self, run, sleep):
        """Save raw responses and invoke only the timestamps device command."""
        with TemporaryDirectory() as directory:
            with patch("get_timestamps_epoch_offset.Path",
                       side_effect=lambda name: Path(directory) / name):
                source = capture_timestamps()
            report = calculate_offset(source.read_text(encoding="utf-8"))
            self.assertEqual(report["sample_count"], 20)
            self.assertTrue((source.parent / "capture.log").exists())
        self.assertEqual(run.call_count, 20)
        self.assertEqual(sleep.call_count, 19)
        self.assertTrue(all(call.args[0] == ["shell", "timestamps"] for call in run.call_args_list))


if __name__ == "__main__":
    unittest.main()
