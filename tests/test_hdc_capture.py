"""Check host and device exit-status handling without executing hdc."""

import io
import subprocess
import unittest
from unittest.mock import patch

from hdc_capture import run_hdc


class HdcCaptureTests(unittest.TestCase):
    """Exercise transport diagnostics and the device status protocol."""

    @patch("hdc_capture.subprocess.run")
    def test_shell_success_requires_device_status(self, run):
        """A successful shell call logs and strips the status marker."""
        run.return_value = subprocess.CompletedProcess([], 0, "payload\n\nOHOS_DFT_EXIT=0\n")
        log = io.StringIO()
        self.assertEqual(run_hdc(["shell", "hitrace --help"], log), "payload\n")
        self.assertIn("ohos_dft_status=$?", run.call_args.args[0][-1])
        self.assertIn("payload", log.getvalue())

    @patch("hdc_capture.subprocess.run")
    def test_device_error_fails_even_when_hdc_returns_zero(self, run):
        """A zero host status cannot hide a device-side command failure."""
        run.return_value = subprocess.CompletedProcess(
            [], 0, "permission denied\nOHOS_DFT_EXIT=7\n"
        )
        with self.assertRaisesRegex(RuntimeError, "Device command exited 7"):
            run_hdc(["shell", "hitrace --trace_begin"], io.StringIO())

    @patch("hdc_capture.subprocess.run")
    def test_missing_status_is_not_assumed_success(self, run):
        """A missing device exit marker is an explicit error."""
        run.return_value = subprocess.CompletedProcess([], 0, "no device\n")
        with self.assertRaisesRegex(RuntimeError, "exit status missing"):
            run_hdc(["shell", "hitrace --help"], io.StringIO())

    @patch("hdc_capture.subprocess.run")
    def test_transfer_uses_hdc_status(self, run):
        """File transfers use the host exit status without shell markers."""
        run.return_value = subprocess.CompletedProcess([], 0, "FileTransfer finish\n")
        self.assertEqual(run_hdc(["file", "recv", "/data/local/tmp/a", "a"], io.StringIO()),
                         "FileTransfer finish\n")

    @patch("hdc_capture.subprocess.run")
    def test_host_failure_includes_diagnostics(self, run):
        """Transport failures preserve the command's diagnostic text."""
        run.return_value = subprocess.CompletedProcess([], 1, "transport error")
        with self.assertRaisesRegex(RuntimeError, "transport error"):
            run_hdc(["shell", "hitrace --help"], io.StringIO())


if __name__ == "__main__":
    unittest.main()
