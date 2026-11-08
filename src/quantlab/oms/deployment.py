"""Paper deployment of a LEAN strategy (LIVE-001 / OMS-007, DEC-0033).

``deploy`` runs one C# strategy on the native LEAN engine in LEAN's real-time (live) mode with every order
routed through QuantLab: LEAN → QuantLabBrokerage → gateway → OMS → risk gate → paper broker. Prices for the
algorithm come from QuantLab's own quote source through the same gateway (QuantLabDataQueue), so the strategy
and the risk gate see the same quotes, with their data state (LIVE / DELAYED / SIMULATED).

Safety: paper only (the OMS accepts only paper adapters); the strategy files are snapshotted and compiled per
deployment; one deployment per name (OS lock); the LEAN command line is fixed (launcher + ``--config`` only)
and the config file must exist (LEAN would otherwise fall back to ``config.json``); start and stop are
audited. The lifecycle check (strategy promoted to ``paper``) is done by the caller (CLI), which sits above
this layer.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from quantlab.core.audit import AuditLog
from quantlab.core.filelock import LockHeld, exclusive
from quantlab.core.logging import get_logger
from quantlab.engine.base import EngineError
from quantlab.engine.lean.native import LAUNCHER, MANIFEST, build_live_config, compile_csharp
from quantlab.oms.gateway import Gateway, write_run_file
from quantlab.oms.service import OMS

log = get_logger(__name__)


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentResult:
    deployment: str
    work_dir: Path
    log_path: Path
    exit_code: int | None
    stopped_by: str  # "algorithm" (LEAN exited), "owner" (stop requested), "time limit"


def deploy(
    oms: OMS,
    sessions: sessionmaker[Session],
    *,
    deployment: str,
    strategy_dir: Path,
    entry_file: str,
    install_dir: Path,
    home: Path,
    stop: threading.Event | None = None,
    max_minutes: float | None = None,
    dotnet: str = "dotnet",
    force_exchange_open: bool = False,
    on_started: Callable[[Path], None] | None = None,
) -> DeploymentResult:
    """Run the strategy until LEAN exits, ``stop`` is set or ``max_minutes`` elapse; always cleans up."""
    if not entry_file.endswith(".cs"):
        raise DeploymentError(
            "paper deployments run C# strategies on the native LEAN engine (Python: KI-0078)"
        )
    if not (install_dir / MANIFEST).is_file() or not (install_dir / "bin" / LAUNCHER).is_file():
        raise DeploymentError(
            f"no native LEAN install at {install_dir}: run `quantlab engine native-install`"
        )
    stop = stop or threading.Event()
    run_file = home / "run" / f"gateway-{deployment}.json"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work = home / "deployments" / deployment / stamp
    audit = AuditLog()
    try:
        lock = exclusive(run_file.with_name(run_file.name + ".lock"))
        lock.__enter__()
    except LockHeld:
        raise DeploymentError(f"deployment {deployment!r} (or its gateway) is already running") from None
    gateway: Gateway | None = None
    thread: threading.Thread | None = None
    proc: subprocess.Popen[bytes] | None = None
    exit_code: int | None = None
    stopped_by = "error"
    started = False
    try:
        gateway = Gateway(oms, sessions, deployment)  # validates the name, binds to loopback
        work.mkdir(parents=True)
        shutil.copytree(strategy_dir, work / "strategy")  # the deployed code is fixed for this run
        try:
            dll = compile_csharp(work / "strategy", work / "build", dotnet=dotnet)
        except EngineError as exc:
            raise DeploymentError(str(exc)) from exc
        write_run_file(run_file, gateway.info)
        cfg = build_live_config(
            algorithm_location=str(dll),
            type_name=Path(entry_file).stem,
            algorithm_id=f"{deployment}-{stamp}",
            gateway_file=str(run_file),
            data_folder=str(install_dir / "Data") + "/",
            results=str(work / "results"),
            storage=str(work / "storage"),
            data_queue_handler="QuantLabDataQueue",
            force_exchange_open=force_exchange_open,
        )
        config = work / "lean-config.json"
        config.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not config.is_file():  # LEAN silently falls back to its own config.json otherwise
            raise DeploymentError(f"LEAN config was not written: {config}")
        (work / "results").mkdir()
        with sessions() as session:
            audit.record(
                session, actor_type="user", actor="owner", action="paper.deploy.start", target=deployment,
                details={"strategy_dir": str(strategy_dir), "entry_file": entry_file, "work_dir": str(work),
                         "account": oms.account},
            )  # fmt: skip
            session.commit()
        started = True
        thread = threading.Thread(target=gateway.serve, name=f"gateway-{deployment}", daemon=True)
        thread.start()
        log_path = work / "engine.log"
        with log_path.open("wb") as log_file:
            proc = subprocess.Popen(
                [dotnet, LAUNCHER, "--config", str(config)],  # fixed command line: no extra LEAN arguments
                cwd=install_dir / "bin",
                stdout=log_file,
                stderr=subprocess.STDOUT,
            )
        if on_started is not None:
            on_started(work)
        deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
        stopped_by = "algorithm"
        while True:
            try:
                exit_code = proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            except KeyboardInterrupt:  # Ctrl+C: the owner stops the deployment (cleaned up below)
                stopped_by = "owner"
                break
            if stop.is_set():
                stopped_by = "owner"
                break
            if deadline is not None and time.monotonic() > deadline:
                stopped_by = "time limit"
                break
        return DeploymentResult(deployment, work, log_path, exit_code, stopped_by)
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                exit_code = proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait()
        if gateway is not None:
            gateway.stop()
        if thread is not None:
            thread.join(timeout=10)
        run_file.unlink(missing_ok=True)  # the token is only valid while the deployment runs
        try:
            if started:
                with sessions() as session:
                    audit.record(
                        session, actor_type="user", actor="owner", action="paper.deploy.stop",
                        target=deployment,
                        details={"exit_code": exit_code, "stopped_by": stopped_by, "work_dir": str(work)},
                    )  # fmt: skip
                    session.commit()
        finally:
            lock.__exit__(None, None, None)
