"""Check filename interval arithmetic, timezone handling and rejection paths."""

import unittest

from get_filename_epoch_offset import calculate_filename_offset


FIRST = "record_trace_20261027224938@331380-537110158.sys"
SECOND = "trace_20261027224939@331380-775500000.sys"
THIRD = "trace_20261027224939@331381-655500000.sys"


class FilenameOffsetTests(unittest.TestCase):
    """Exercise interval bounds without collecting device data."""

    def test_single_filename_retains_full_second_uncertainty(self):
        """One filename cannot recover its missing subsecond wall time."""
        report = calculate_filename_offset(FIRST)
        self.assertEqual(report["offset_lower_inclusive_nanoseconds"], "1792781197462889842")
        self.assertEqual(report["interval_width_nanoseconds"], 1_000_000_000)
        self.assertEqual(report["quantization_uncertainty_nanoseconds"], 500_000_000)

    def test_intersection_shrinks_and_contains_known_offset(self):
        """Independent phases narrow the interval around a known offset."""
        report = calculate_filename_offset("\n".join([FIRST, SECOND, THIRD]))
        self.assertEqual(report["interval_width_nanoseconds"], 120_000_000)
        self.assertEqual(report["epoch_minus_kernel_seconds"], "1792781198.284500000")
        truth = 1792781198324500000
        self.assertLessEqual(int(report["offset_lower_inclusive_nanoseconds"]), truth)
        self.assertGreater(int(report["offset_upper_exclusive_nanoseconds"]), truth)

    def test_duplicate_paths_add_no_information(self):
        """Repeated names are counted once even when embedded in paths."""
        report = calculate_filename_offset(FIRST + "\n/data/log/hitrace/" + FIRST)
        self.assertEqual(report["filename_count"], 1)

    def test_nanosecond_integer_need_not_have_leading_zeros(self):
        """A short nanosecond field is an integer, not a decimal fraction."""
        report = calculate_filename_offset("record_trace_20261027234907@334949-18089377.sys")
        self.assertEqual(report["sync_points"][0]["boot_nanoseconds"], "334949018089377")

    def test_inconsistent_intervals_fail_without_averaging(self):
        """Incompatible intervals cannot produce a fixed-offset estimate."""
        with self.assertRaisesRegex(ValueError, "no intersection"):
            calculate_filename_offset(FIRST + "\n" + FIRST.replace("224938", "224941"))

    def test_empty_input_does_not_use_other_time_sources(self):
        """Missing names fail instead of guessing from another clock."""
        with self.assertRaisesRegex(ValueError, "No hitrace filename"):
            calculate_filename_offset("no filename")

    def test_epoch_to_boot_direction(self):
        """Epoch-to-boot conversion subtracts the reported offset."""
        report = calculate_filename_offset("\n".join([FIRST, SECOND, THIRD]))
        self.assertEqual(1793112579000000000 - int(report["epoch_minus_kernel_nanoseconds"]),
                         331380715500000)

    def test_explicit_device_timezone(self):
        """The supplied device timezone controls local-to-epoch conversion."""
        china = calculate_filename_offset(FIRST)
        utc = calculate_filename_offset(FIRST, "+00:00")
        self.assertEqual(int(utc["epoch_minus_kernel_nanoseconds"])
                         - int(china["epoch_minus_kernel_nanoseconds"]), 28800_000_000_000)

    def test_invalid_timezone_is_rejected(self):
        """Out-of-range timezone minutes are rejected."""
        with self.assertRaisesRegex(ValueError, "UTC offset"):
            calculate_filename_offset(FIRST, "+08:99")


if __name__ == "__main__":
    unittest.main()
