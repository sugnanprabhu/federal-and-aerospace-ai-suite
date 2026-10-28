# SPDX-FileCopyrightText: (C) 2026 Intel Corporation
# SPDX-License-Identifier: Apache-2.0
"""Pure functions of scripts/mavlink_pipeline_manager.py (run with tests/camera_config/run_tests.sh)."""
import os
import sys
import unittest

REPO = os.environ.get("REPO_DIR", os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
sys.path.insert(0, os.path.join(REPO, "scripts"))
import mavlink_pipeline_manager as manager  # noqa: E402


class ArmedFromPayload(unittest.TestCase):
    def test_status_messages(self):
        self.assertIs(manager.armed_from_payload(b'{"armed": true, "mode": "HOLD"}'), True)
        self.assertIs(manager.armed_from_payload('{"armed": false}'), False)

    def test_unusable_messages_are_ignored(self):
        for bad in (b"", b"not json", b"[]", b'{"mode": "HOLD"}', None, b"null"):
            self.assertIsNone(manager.armed_from_payload(bad), bad)


if __name__ == "__main__":
    unittest.main(verbosity=2)
