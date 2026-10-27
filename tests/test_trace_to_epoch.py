"""Check timestamp conversion and preservation of trace event content."""

import unittest

from trace_to_epoch import convert_text


EVENT = "          worker-42 ( 42) [001] .... "
ANCHOR = (
    EVENT + "332767.424788: tracing_mark_write: "
    "trace_event_clock_sync: realtime_ts=1793113965749\n"
)


class TraceToEpochTests(unittest.TestCase):
    """Exercise derived trace output without modifying input files."""

    def test_exact_epoch_arithmetic_and_event_content(self):
        """Only header timestamps change, with exact decimal arithmetic."""
        original = (
            "# tracer: nop\n"
            + EVENT + "332767.123456789: tracing_mark_write: payload=332767.123456789\n"
            + ANCHOR
        )
        converted, report = convert_text(original)
        self.assertIn(
            EVENT + "1793113965.447668789: tracing_mark_write: payload=332767.123456789\n",
            converted,
        )
        self.assertIn(EVENT + "1793113965.749000000: tracing_mark_write:", converted)
        self.assertIn("# tracer: nop\n", converted)
        self.assertEqual(report["events_converted"], 2)
        self.assertEqual(report["epoch_minus_kernel_seconds"], "1792781198.324212000")

    def test_missing_sync_is_an_error(self):
        """Conversion requires an explicit realtime synchronization point."""
        with self.assertRaisesRegex(ValueError, "No realtime_ts"):
            convert_text(EVENT + "332767.123456: sched_switch: prev_pid=42\n")

    def test_inconsistent_syncs_are_an_error(self):
        """Conversion rejects a trace whose marker offsets disagree."""
        with self.assertRaisesRegex(ValueError, "fixed offset was not accepted"):
            convert_text(ANCHOR + ANCHOR.replace("424788", "524788"))

    def test_multiple_syncs_use_the_calibration_estimate(self):
        """Multiple markers use the same estimator as the offset tool."""
        second = ANCHOR.replace("332767.424788", "332768.424888").replace(
            "1793113965749", "1793113966749"
        )
        converted, report = convert_text(ANCHOR + second)
        self.assertEqual(report["sync_point_count"], 2)
        self.assertEqual(report["epoch_minus_kernel_seconds"], "1792781198.324212000")
        self.assertIn(EVENT + "1793113966.749100000: tracing_mark_write:", converted)

    def test_repeated_conversion_is_an_error(self):
        """The output header prevents a second epoch shift."""
        converted, _ = convert_text(ANCHOR)
        with self.assertRaisesRegex(ValueError, "already contains epoch"):
            convert_text(converted)


if __name__ == "__main__":
    unittest.main()
