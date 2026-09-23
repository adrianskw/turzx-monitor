"""Connection failures should release the port and allow a clean shutdown."""

import unittest
from unittest.mock import Mock, patch

from turzx.app import App
from turzx.driver import TurzxDisplay


class DisplayConnectionTests(unittest.TestCase):
    @patch("turzx.driver.find_port", return_value=None)
    def test_missing_display_is_retryable(self, find_port):
        with self.assertRaisesRegex(OSError, "not found"):
            TurzxDisplay()

    @patch("turzx.driver.serial.Serial")
    @patch.object(TurzxDisplay, "_hello", side_effect=OSError("no reply"))
    def test_failed_handshake_closes_serial_port(self, hello, serial):
        with self.assertRaisesRegex(OSError, "no reply"):
            TurzxDisplay(port="/dev/test")
        serial.return_value.close.assert_called_once_with()


class ReconnectTests(unittest.TestCase):
    def test_shutdown_during_retry_sleep_stops_reconnect(self):
        app = App({}, [], None)
        display = app.display = Mock()

        def stop(_seconds):
            app.running = False

        with patch("turzx.app.time.sleep", side_effect=stop), patch.object(app, "connect") as connect:
            app._reconnect()

        display.close.assert_called_once_with()
        self.assertIsNone(app.display)
        connect.assert_not_called()

    def test_run_stops_after_failed_initial_connection(self):
        app = App({}, [], None)

        def stop():
            app.running = False

        with patch.object(app, "connect", side_effect=OSError("missing")), patch.object(
            app, "_reconnect", side_effect=stop
        ), patch.object(app, "flush") as flush:
            app.run(once=True)

        flush.assert_not_called()


if __name__ == "__main__":
    unittest.main()
