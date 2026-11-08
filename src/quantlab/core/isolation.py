"""Network isolation for strategy processes (KI-0082, DEC-0035).

On Linux (including WSL2) a deployment's LEAN process runs in its own network namespace (``unshare --user
--map-root-user --net``): it has no network interface but a down loopback, so it cannot reach IB Gateway, the
internet or any service on the host, only files. The QuantLab gateway is reached through a Unix socket file.
The kernel enforces this; strategy code cannot opt out.

``probe`` checks the real thing before every deployment: it starts a process the same way and verifies that a
TCP connection to a listener on the host's loopback fails from inside. Other systems (Windows, macOS) have no
equivalent without administrator rights; there the result says so and the caller decides (DEC-0035).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from dataclasses import dataclass

UNSHARE = ("--user", "--map-root-user", "--net", "--")

_PROBE = """
import socket, sys
s = socket.socket()
s.settimeout(2)
code = s.connect_ex(("127.0.0.1", int(sys.argv[1])))
print("REACHED" if code == 0 else "ISOLATED")
"""


@dataclass(frozen=True)
class IsolationProbe:
    supported: bool
    detail: str
    prefix: tuple[str, ...] = ()  # put in front of the command line to run it isolated


def probe(timeout_s: float = 30.0) -> IsolationProbe:
    if not sys.platform.startswith("linux"):
        return IsolationProbe(False, f"no network isolation on {sys.platform} (Linux / WSL2 only)")
    unshare = shutil.which("unshare")
    if unshare is None:
        return IsolationProbe(False, "`unshare` (util-linux) not found")
    prefix = (unshare, *UNSHARE)
    with socket.create_server(("127.0.0.1", 0)) as listener:  # a host service the child must not reach
        port = listener.getsockname()[1]
        try:
            done = subprocess.run(
                [*prefix, sys.executable, "-I", "-c", _PROBE, str(port)],
                capture_output=True, text=True, timeout=timeout_s, check=False,
            )  # fmt: skip
        except (OSError, subprocess.TimeoutExpired) as exc:
            return IsolationProbe(False, f"network namespace could not be started: {exc}")
    out = done.stdout.strip()
    if done.returncode != 0:
        reason = (done.stderr.strip() or out)[-300:]
        return IsolationProbe(
            False,
            f"network namespace could not be started ({reason}); unprivileged user namespaces may be "
            "disabled (e.g. Ubuntu: sysctl kernel.apparmor_restrict_unprivileged_userns)",
        )
    if out != "ISOLATED":
        return IsolationProbe(False, f"isolation check failed: the probe reached the host ({out!r})")
    return IsolationProbe(True, "network namespace: no network, host loopback unreachable", prefix)
