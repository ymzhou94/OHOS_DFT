"""Check exact marker arithmetic and fixed-offset validation."""

import unittest

from get_trace_epoch_offset import calculate_offset


def sync(kernel, epoch_ms):
    """Create a synthetic realtime synchronization marker."""
    return (f"   hitrace-42 ( 42) [001] .... {kernel}: tracing_mark_write: "
            f"trace_event_clock_sync: realtime_ts={epoch_ms}\n")


class OffsetTests(unittest.TestCase):
    """Exercise calibration without hdc or a connected device."""

    def test_exact_offset_and_conversion_direction(self):
        """Offset arithmetic preserves units and conversion direction."""
        report = calculate_offset([sync("332767.424788", 1793113965749)])
        self.assertEqual(report["epoch_minus_kernel_seconds"], "1792781198.324212000")
        offset_ns = int(report["epoch_minus_kernel_nanoseconds"])
        self.assertEqual(1793113965749000000 - offset_ns, 332767424788000)
        self.assertEqual(report["consistency"], "single_point_unverified")

    def test_multiple_consistent_points(self):
        """Consistent samples retain exact range and median statistics."""
        report = calculate_offset([
            sync("333713.874246", 1793114912198),
            sync("333714.781093", 1793114913105),
        ])
        self.assertEqual(report["sync_point_count"], 2)
        self.assertEqual(report["offset_spread_nanoseconds"], 153000)
        self.assertEqual(report["consistency"], "multiple_points_within_1ms")
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "1792781198323907000")
        self.assertEqual(report["offset_min_nanoseconds"], "1792781198323754000")
        self.assertEqual(report["offset_median_nanoseconds"], "1792781198323830500")

    def test_epoch_step_rejects_fixed_offset(self):
        """A large epoch step prevents accepting a fixed offset."""
        with self.assertRaisesRegex(ValueError, "fixed offset was not accepted"):
            calculate_offset([
                sync("333712.781093", 1793114911105),
                sync("333713.781093", 1793114913105),
            ])

    def test_missing_sync_has_no_filename_fallback(self):
        """A filename alone cannot substitute for a required sync marker."""
        with self.assertRaisesRegex(ValueError, "No realtime_ts"):
            calculate_offset(["trace_20261027224938@331380-537110158.sys\n"])

    def test_nanoseconds_survive_large_epoch_subtraction(self):
        """Large epoch values retain low-order nanosecond digits."""
        report = calculate_offset([sync("332767.424788123", 1793113965749)])
        self.assertEqual(report["epoch_minus_kernel_nanoseconds"], "1792781198324211877")

    def test_even_sample_median_does_not_use_float(self):
        """An even-sized sample retains an exact fractional median."""
        report = calculate_offset([
            sync("332767.424788123", 1793113965749),
            sync("332768.424788124", 1793113966749),
        ])
        self.assertEqual(report["offset_median_nanoseconds"], "1792781198324211876.5")

    def test_converted_trace_is_rejected(self):
        """An already converted trace is not treated as a kernel clock."""
        with self.assertRaisesRegex(ValueError, "already has epoch"):
            calculate_offset(["# trace_to_epoch: timestamps=epoch; unit=seconds\n"])

    def test_malformed_anchor_is_not_silently_skipped(self):
        """Malformed markers fail even when another valid marker exists."""
        with self.assertRaisesRegex(ValueError, "Malformed realtime_ts.*line 2"):
            calculate_offset([
                sync("332767.424788", 1793113965749),
                "trace_event_clock_sync: realtime_ts=not_a_timestamp\n",
            ])


if __name__ == "__main__":
    unittest.main()
