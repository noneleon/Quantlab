"""Unit tests for tools/env_check.py (MEM-003)."""

import importlib.util
import json
import socket
import sys
import unittest
from pathlib import Path
from unittest import mock

_PATH = Path(__file__).resolve().parents[2] / "tools" / "env_check.py"
_spec = importlib.util.spec_from_file_location("env_check", _PATH)
env_check = importlib.util.module_from_spec(_spec)
sys.modules["env_check"] = env_check  # dataclasses resolve types via sys.modules
_spec.loader.exec_module(env_check)


class ParseVersionTests(unittest.TestCase):
    def test_extracts_versions(self):
        self.assertEqual(env_check.parse_version("Docker version 29.9.2, build a82e528"), "29.9.2")
        self.assertEqual(env_check.parse_version("git version 2.43.0"), "2.43.0")
        self.assertEqual(env_check.parse_version("v20.11"), "20.11")
        self.assertIsNone(env_check.parse_version("no digits here"))
        self.assertIsNone(env_check.parse_version(""))

    def test_major(self):
        self.assertEqual(env_check.major("10.0.100"), 10)
        self.assertIsNone(env_check.major(None))


class CheckTests(unittest.TestCase):
    def test_missing_binary_reports_not_found(self):
        with mock.patch.object(env_check.shutil, "which", return_value=None):
            self.assertEqual(env_check.run(["definitely-missing"]), (127, ""))
            self.assertFalse(env_check.check_dotnet().found)
            self.assertFalse(env_check.check_docker().found)

    def test_dotnet_requires_target_major(self):
        out = "8.0.400 [C:\\sdk]\n10.0.100 [C:\\sdk]"
        with mock.patch.object(env_check, "run", return_value=(0, out)):
            c = env_check.check_dotnet()
        self.assertTrue(c.ok)
        with mock.patch.object(env_check, "run", return_value=(0, "8.0.400 [x]")):
            self.assertFalse(env_check.check_dotnet().ok)

    def test_docker_daemon_unreachable_is_not_ok(self):
        responses = {("docker", "--version"): (0, "Docker version 29.9.2"),
                     ("docker", "info"): (1, "Cannot connect to the Docker daemon")}
        with mock.patch.object(env_check, "run", side_effect=lambda cmd: responses[tuple(cmd[:2])]):
            c = env_check.check_docker()
        self.assertTrue(c.found)
        self.assertFalse(c.ok)
        self.assertIn("daemon NOT reachable", c.detail)

    def test_python_check_flags_non_311(self):
        c = env_check.check_python()
        self.assertTrue(c.found)
        self.assertEqual(c.ok, tuple(map(int, c.version.split(".")[:2])) == env_check.LEAN_PYTHON)

    def test_port_open_detects_listener(self):
        with socket.socket() as srv:
            srv.bind(("127.0.0.1", 0))
            srv.listen(1)
            port = srv.getsockname()[1]
            self.assertTrue(env_check.port_open(port))
        self.assertFalse(env_check.port_open(port))


class ReportTests(unittest.TestCase):
    def test_collect_is_json_serialisable_and_renders(self):
        report = env_check.collect()
        json.dumps(report)
        names = {c["name"] for c in report["checks"]}
        self.assertTrue({"python", "dotnet", "docker", "git", "ibkr_api"} <= names)
        self.assertIn("QuantLab environment check", env_check.render(report))


if __name__ == "__main__":
    unittest.main()
