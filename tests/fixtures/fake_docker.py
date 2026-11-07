"""Stand-in for the `docker` CLI used by launcher tests (no Docker daemon needed).

Behaviour is controlled by env vars:
  FAKE_DOCKER_STATE  directory for invocation log + pid files (required)
  FAKE_DOCKER_EXIT   exit code for `run` (default 0)
  FAKE_DOCKER_SLEEP  seconds `run` sleeps before finishing (default 0)
  FAKE_DOCKER_DAEMON "0" makes `version` fail like an unreachable daemon
  FAKE_DOCKER_IMAGE  "0" makes `image inspect` fail like a missing image
  FAKE_DOCKER_EQUITY_SHIFT  add this amount to the last equity point (simulated result change)
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
    if "/QuantLab/dataset" in m:  # record what a dataset run would see (tests assert on it)
        root = m["/QuantLab/dataset"]
        listing = sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())
        (STATE / f"{config['algorithm-id']}-dataset.json").write_text(json.dumps(listing))
    print(
        f"TRACE:: Engine.Main(): LEAN ALGORITHMIC TRADING ENGINE (fake) algorithm-id={config['algorithm-id']}"
    )
    print("password=should-not-matter-here")
    sys.stdout.flush()
    time.sleep(float(os.environ.get("FAKE_DOCKER_SLEEP", "0")))
    library = m.get("/QuantLab/library")
    algorithm_text = (m["/LeanCLI"] / config["algorithm-location"].rsplit("/", 1)[-1]).read_text()
    on_path = "/QuantLab/library" in config.get("python-additional-paths", [])
    if library is not None and on_path and "quantlab_lean.configure" in algorithm_text:
        # run the REAL helper against the recording stub
        sys.path[:0] = [str(Path(__file__).parent / "lean_stub"), str(library)]
        import AlgorithmImports as stub
        import quantlab_lean

        algo = stub.StubAlgorithm()
        quantlab_lean.configure(algo)
        security = stub.StubSecurity()
        algo.initializer.initialize(security)
        (STATE / f"{config['algorithm-id']}-models.json").write_text(
            json.dumps({"brokerage": algo.brokerage_model, "security": security.events}, default=str)
        )
        (m["/Results"] / f"{config['algorithm-id']}-log.txt").write_text("\n".join(algo.logs) + "\n")
    sample = Path(__file__).parent / "lean" / "sample_result.json"
    result = json.loads(sample.read_text())
    shift = float(os.environ.get("FAKE_DOCKER_EQUITY_SHIFT", "0"))  # simulate an engine/result change
    if shift:
        values = result["charts"]["Strategy Equity"]["series"]["Equity"]["values"]
        values[-1] = [values[-1][0], values[-1][-1] + shift]
    (m["/Results"] / f"{config['algorithm-id']}.json").write_text(json.dumps(result))
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
