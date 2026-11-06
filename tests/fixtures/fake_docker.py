"""Stand-in for the `docker` CLI used by launcher tests (no Docker daemon needed).

Behaviour is controlled by env vars:
  FAKE_DOCKER_STATE  directory for invocation log + pid files (required)
  FAKE_DOCKER_EXIT   exit code for `run` (default 0)
  FAKE_DOCKER_SLEEP  seconds `run` sleeps before finishing (default 0)
  FAKE_DOCKER_DAEMON "0" makes `version` fail like an unreachable daemon
  FAKE_DOCKER_IMAGE  "0" makes `image inspect` fail like a missing image
`run` reads the mounted config.json, then writes <algorithm-id>.json into the mounted /Results folder
from tests/fixtures/lean/sample_result.json — imitating LEAN's file layout, with synthetic content.
"""

import contextlib
import json
import os
import signal
import sys
import time
from pathlib import Path

STATE = Path(os.environ["FAKE_DOCKER_STATE"])
STATE.mkdir(parents=True, exist_ok=True)
args = sys.argv[1:]
with (STATE / "calls.jsonl").open("a", encoding="utf-8") as f:
    f.write(json.dumps(args) + "\n")


def mounts(argv):
    out = {}
    for i, a in enumerate(argv):
        if a == "--mount":
            parts = dict(p.split("=", 1) for p in argv[i + 1].split(",") if "=" in p)
            out[parts["target"]] = Path(parts["source"])
    return out


if args[:1] == ["version"]:
    if os.environ.get("FAKE_DOCKER_DAEMON") == "0":
        print("Cannot connect to the Docker daemon", file=sys.stderr)
        sys.exit(1)
    print("29.9.2")
elif args[:2] == ["image", "inspect"]:
    if os.environ.get("FAKE_DOCKER_IMAGE") == "0":
        print("Error: No such image", file=sys.stderr)
        sys.exit(1)
    print(
        json.dumps({"Id": "sha256:" + "b" * 64, "RepoDigests": [args[-1]], "Created": "2026-11-06T17:54:41Z"})
    )
elif args[:1] == ["run"]:
    name = args[args.index("--name") + 1]
    (STATE / f"{name}.pid").write_text(str(os.getpid()))
    m = mounts(args)
    config = json.loads(m["/Lean/Launcher/bin/Debug/config.json"].read_text())
    print(
        f"TRACE:: Engine.Main(): LEAN ALGORITHMIC TRADING ENGINE (fake) algorithm-id={config['algorithm-id']}"
    )
    print("password=should-not-matter-here")
    sys.stdout.flush()
    time.sleep(float(os.environ.get("FAKE_DOCKER_SLEEP", "0")))
    sample = Path(__file__).parent / "lean" / "sample_result.json"
    (m["/Results"] / f"{config['algorithm-id']}.json").write_text(sample.read_text())
    sys.exit(int(os.environ.get("FAKE_DOCKER_EXIT", "0")))
elif args[:1] == ["stop"]:
    pid_file = STATE / f"{args[-1]}.pid"
    if pid_file.exists():
        with contextlib.suppress(OSError):
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
elif args[:1] == ["pull"]:
    print("pulled")
else:
    print(f"fake docker: unsupported {args}", file=sys.stderr)
    sys.exit(2)
