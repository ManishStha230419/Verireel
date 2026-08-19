import logging
import unittest
from pathlib import Path
from unittest.mock import patch

from utils.audit import audit_event, build_security_logger


class AuditLoggerTests(unittest.TestCase):
    def test_inaccessible_log_directory_falls_back_without_blocking_startup(self):
        logger = logging.Logger("verireel.security.test")
        with (
            patch("utils.audit.logging.getLogger", return_value=logger),
            patch.object(Path, "mkdir", side_effect=PermissionError("denied")),
        ):
            built = build_security_logger(Path("unavailable"))

        self.assertIs(built, logger)
        self.assertEqual(len(logger.handlers), 1)
        self.assertIsInstance(logger.handlers[0], logging.StreamHandler)
        audit_event(logger, "startup_fallback", status="ok")


if __name__ == "__main__":
    unittest.main()
