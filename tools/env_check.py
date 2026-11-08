#!/usr/bin/env python3
"""QuantLab environment probe (MEM-003).

Reports the toolchain QuantLab depends on: Python, .NET, Docker (client + daemon),
Git, Lean CLI, WSL (Windows), and whether IB Gateway / TWS API ports are listening.

Stdlib only so it runs on a fresh Windows or Linux machine before any setup:

    python tools/env_check.py          # human-readable table
    python tools/env_check.py --json   # machine-readable

It only reads information; it never installs, starts or changes anything.
"""

from __future__ import annotations

import argparse
import json
import platform
import re
import shutil
import socket
import subprocess
import sys
from dataclasses import asdict, dataclass, field

# Versions QuantLab targets (see docs/ARCHITECTURE.md §1.1).
LEAN_PYTHON = (3, 11)
LEAN_DOTNET_MAJOR = 10

# Default TWS / IB Gateway API ports (paper and live).
IB_PORTS = {
    "ib_gateway_paper": 4002,
    "ib_gateway_live": 4001,
    "tws_paper": 7497,
    "tws_live": 7496,
}


@dataclass
class Check:
    name: str
    found: bool
    version: str | None = None
    ok: bool = False
    detail: str = ""
    extra: dict = field(default_factory=dict)


def run(cmd: list[str], timeout: float = 15.0) -> tuple[int, str]:
    """Run a command, returning (exit_code, combined output). Missing binary -> (127, '')."""
    exe = shutil.which(cmd[0])
    if exe is None:
        return 127, ""
    try:
        proc = subprocess.run(
            [exe, *cmd[1:]],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def parse_version(text: str) -> str | None:
    """Return the first dotted version number in text, e.g. 'Docker version 29.9.2' -> '29.9.2'."""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", text or "")
    return match.group(1) if match else None


def major(version: str | None) -> int | None:
    if not version:
        return None
    try:
        return int(version.split(".")[0])
    except ValueError:
        return None


def check_python() -> Check:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    ok = (v.major, v.minor) == LEAN_PYTHON
    detail = (
        "matches LEAN's embedded Python"
        if ok
        else f"QuantLab backend targets Python {LEAN_PYTHON[0]}.{LEAN_PYTHON[1]} "
        "(LEAN pythonnet); install it e.g. with `uv python install 3.11`"
    )
    return Check("python", True, version, ok, detail, {"executable": sys.executable})


def check_dotnet() -> Check:
    code, out = run(["dotnet", "--list-sdks"])
    if code == 127:
        return Check(
            "dotnet",
            False,
            detail=f".NET SDK {LEAN_DOTNET_MAJOR} needed only for the native LEAN launcher or C# algorithms",
        )
    sdks = [parse_version(line) for line in out.splitlines() if parse_version(line)]
    has_target = any(major(s) == LEAN_DOTNET_MAJOR for s in sdks)
    return Check(
        "dotnet",
        True,
        ", ".join(s for s in sdks if s) or None,
        has_target,
        "ok" if has_target else f"no .NET {LEAN_DOTNET_MAJOR} SDK found",
    )


def check_docker() -> Check:
    code, out = run(["docker", "--version"])
    if code == 127:
        return Check("docker", False, detail="Docker needed for the default LEAN launcher")
    version = parse_version(out)
    dcode, dout = run(["docker", "info", "--format", "{{.ServerVersion}}"])
    daemon = dcode == 0 and bool(parse_version(dout))
    return Check(
        "docker",
        True,
        version,
        daemon,
        "daemon reachable"
        if daemon
        else "client found but daemon NOT reachable (start Docker Desktop / dockerd)",
        {"daemon_version": parse_version(dout) if daemon else None},
    )


def check_simple(name: str, cmd: list[str], why: str) -> Check:
    code, out = run(cmd)
    if code == 127:
        return Check(name, False, detail=why)
    version = parse_version(out)
    return Check(name, True, version, code == 0, "ok" if code == 0 else out[:200])


def check_wsl() -> Check | None:
    if platform.system() != "Windows":
        return None
    code, out = run(["wsl", "--status"])
    if code == 127:
        return Check("wsl", False, detail="WSL2 is required by Docker Desktop's recommended backend")
    # `wsl --status` prints UTF-16 on some builds; strip NULs before inspecting.
    clean = out.replace("\x00", "")
    return Check("wsl", True, parse_version(clean), code == 0, clean.splitlines()[0] if clean else "")


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ib_ports() -> Check:
    status = {name: port_open(port) for name, port in IB_PORTS.items()}
    listening = [f"{n}:{IB_PORTS[n]}" for n, up in status.items() if up]
    return Check(
        "ibkr_api",
        bool(listening),
        ok=bool(listening),
        detail=("listening: " + ", ".join(listening))
        if listening
        else "no TWS/IB Gateway API port listening (only needed for paper/live trading)",
        extra=status,
    )


def collect() -> dict:
    checks = [
        check_python(),
        check_dotnet(),
        check_docker(),
        check_simple("git", ["git", "--version"], "Git is required"),
        check_simple("uv", ["uv", "--version"], "uv recommended for Python 3.11 + lockfiles"),
        check_simple("lean_cli", ["lean", "--version"], "optional; QuantLab does not require Lean CLI"),
        check_simple("node", ["node", "--version"], "Node.js needed for the web UI (Phase 2)"),
    ]
    wsl = check_wsl()
    if wsl is not None:
        checks.append(wsl)
    checks.append(check_ib_ports())
    return {
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "version": platform.version(),
            "machine": platform.machine(),
        },
        "checks": [asdict(c) for c in checks],
    }


def render(report: dict) -> str:
    p = report["platform"]
    lines = [f"QuantLab environment check — {p['system']} {p['release']} ({p['machine']})", ""]
    lines.append(f"{'component':<10} {'found':<6} {'ok':<4} {'version':<22} detail")
    lines.append("-" * 90)
    for c in report["checks"]:
        lines.append(
            f"{c['name']:<10} {'yes' if c['found'] else 'no':<6} {'yes' if c['ok'] else 'no':<4} "
            f"{(c['version'] or '-'):<22} {c['detail']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = parser.parse_args(argv)
    report = collect()
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
