"""Runtime state must not depend on an editable checkout or installation path."""

import os
import unittest
from pathlib import Path
from unittest.mock import patch

from golf_austria.browser import default_state_directory


class StatePathTests(unittest.TestCase):
    def test_default_is_user_state_not_package_directory(self):
        with (
            patch.dict(os.environ, {}, clear=True),
            patch.object(Path, "home", return_value=Path("/fictional-home")),
        ):
            self.assertEqual(
                default_state_directory(), Path("/fictional-home/.local/state/golf-austria")
            )

    def test_absolute_xdg_state_home_is_respected(self):
        with patch.dict(os.environ, {"XDG_STATE_HOME": "/fictional-state"}, clear=True):
            self.assertEqual(default_state_directory(), Path("/fictional-state/golf-austria"))

    def test_relative_xdg_state_home_does_not_put_state_in_checkout(self):
        with (
            patch.dict(os.environ, {"XDG_STATE_HOME": "relative"}, clear=True),
            patch.object(Path, "home", return_value=Path("/fictional-home")),
        ):
            self.assertEqual(
                default_state_directory(), Path("/fictional-home/.local/state/golf-austria")
            )

    def test_explicit_golf_state_override_takes_priority(self):
        with patch.dict(
            os.environ,
            {"GOLF_STATE_DIR": "/chosen-state", "XDG_STATE_HOME": "/ignored-state"},
            clear=True,
        ):
            self.assertEqual(default_state_directory(), Path("/chosen-state"))


if __name__ == "__main__":
    unittest.main()
