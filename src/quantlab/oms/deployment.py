"""Paper deployment of a LEAN strategy (LIVE-001 / OMS-007, DEC-0033).

``deploy`` runs one C# or Python strategy on the native LEAN engine in LEAN's real-time (live) mode with
every order routed through QuantLab: LEAN → QuantLabBrokerage → gateway → OMS → risk gate → paper broker.
Prices for the algorithm come from QuantLab's own quote source through the same gateway (QuantLabDataQueue),
so the strategy and the risk gate see the same quotes, with their data state (LIVE / DELAYED / SIMULATED).

Safety: paper only (the OMS accepts only paper adapters); the strategy files are snapshotted and compiled per
deployment; one deployment per name (OS lock); the LEAN command line is fixed (launcher + ``--config`` only)
and the config file must exist (LEAN would otherwise fall back to ``config.json``); start and stop are
audited. The lifecycle check (strategy promoted to ``paper``) is done by the caller (CLI), which sits above
this layer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from quantlab.core.audit import AuditLog
from quantlab.core.filelock import LockHeld, exclusive
from quantlab.core.logging import get_logger
from quantlab.engine.base import EngineError
from quantlab.engine.lean.native import (
    LAUNCHER,
    MANIFEST,
    PYTHON_SUPPORT,
    build_live_config,
    compile_csharp,
    python_runtime,
)
from quantlab.oms.gateway import Gateway, write_run_file
from quantlab.oms.service import OMS
from quantlab.strategies.snapshot import hash_tree

log = get_logger(__name__)


HEARTBEAT_S = 5.0


class DeploymentError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeploymentResult:
    deployment: str
    work_dir: Path
    log_path: Path
    exit_code: int | None
    stopped_by: str  # "algorithm" (LEAN exited), "owner", "time limit", "gateway failure", "error"


def deploy(
    oms: OMS,
    sessions: sessionmaker[Session],
    *,
    deployment: str,
    strategy: str,
    strategy_dir: Path,
    entry_file: str,
    approved_tree_hash: str,
    install_dir: Path,
    home: Path,
    actor: str = "owner",
    stop: threading.Event | None = None,
    max_minutes: float | None = None,
    dotnet: str = "dotnet",
    force_exchange_open: bool = False,
    on_started: Callable[[Path], None] | None = None,
) -> DeploymentResult:
    """Run the strategy until LEAN exits, ``stop`` is set or ``max_minutes`` elapse; always cleans up.

    ``approved_tree_hash`` is the file hash the strategy was promoted to ``paper`` with (lifecycle, DEC-0026);
    the snapshot that is compiled and run must match it exactly, so nothing unapproved is ever deployed.
    """
    if not entry_file.endswith((".cs", ".py")):
        raise DeploymentError("paper deployments run C# (.cs) or Python (.py) strategies on native LEAN")
    python = entry_file.endswith(".py")
    if not (install_dir / MANIFEST).is_file() or not (install_dir / "bin" / LAUNCHER).is_file():
        raise DeploymentError(
            f"no native LEAN install at {install_dir}: run `quantlab engine native-install`"
        )
    if python and not (install_dir / "bin" / PYTHON_SUPPORT).is_file():
        raise DeploymentError(
            "this native LEAN install predates Python support: re-run `quantlab engine native-install`"
        )
    try:
        env = {**os.environ, **python_runtime()} if python else None
    except EngineError as exc:
        raise DeploymentError(str(exc)) from exc
    stop = stop or threading.Event()
    run_file = home / "run" / f"gateway-{deployment}.json"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    work = home / "deployments" / deployment / stamp
    audit = AuditLog()
    with ExitStack() as cleanup:
        try:
            cleanup.enter_context(exclusive(run_file.with_name(run_file.name + ".lock")))
        except LockHeld:
            raise DeploymentError(f"deployment {deployment!r} (or its gateway) is already running") from None
        state = _Running()
        cleanup.callback(_finish, state, run_file, sessions, audit, deployment, work)  # runs on every exit
        state.gateway = Gateway(oms, sessions, deployment)  # validates the name, binds to loopback
        work.mkdir(parents=True)
        shutil.copytree(strategy_dir, work / "strategy")  # the deployed code is fixed for this run
        snapshot_hash = hash_tree(work / "strategy").tree_hash
        if snapshot_hash != approved_tree_hash:
            raise DeploymentError(
                f"the strategy files ({snapshot_hash[:12]}) are not the approved version "
                f"({approved_tree_hash[:12]}): re-promote to 'paper' before deploying"
            )
        if python:  # LEAN's pythonnet runs the approved snapshot directly
            location = work / "strategy" / entry_file
        else:
            try:
                location = compile_csharp(work / "strategy", work / "build", dotnet=dotnet)
            except EngineError as exc:
                raise DeploymentError(str(exc)) from exc
        write_run_file(run_file, state.gateway.info)
        cfg = build_live_config(
            algorithm_location=str(location),
            type_name=Path(entry_file).stem,
            algorithm_id=f"{deployment}-{stamp}",
            gateway_file=str(run_file),
            data_folder=str(install_dir / "Data") + "/",
            results=str(work / "results"),
            storage=str(work / "storage"),
            data_queue_handler="QuantLabDataQueue",
            language="Python" if python else "CSharp",
            force_exchange_open=force_exchange_open,
        )
        config = work / "lean-config.json"
        config.write_text(json.dumps(cfg, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        if not config.is_file():  # LEAN silently falls back to its own config.json otherwise
            raise DeploymentError(f"LEAN config was not written: {config}")
        (work / "results").mkdir()
        with sessions() as session:
            audit.record(
                session, actor_type="user", actor=actor, action="paper.deploy.start", target=deployment,
                details={"strategy": strategy, "tree_hash": snapshot_hash, "entry_file": entry_file,
                         "work_dir": str(work), "account": oms.account},
            )  # fmt: skip
            session.commit()
        state.started, state.actor = True, actor
        state.status = {
            "deployment": deployment,
            "strategy": strategy,
            "tree_hash": snapshot_hash,
            "account": oms.account,
            "mode": "paper",
            "state": "running",
            "started_at": datetime.now(UTC).isoformat(),
        }
        _write_status(state, work)
        state.thread = threading.Thread(target=state.gateway.serve, name=f"gateway-{deployment}", daemon=True)
        state.thread.start()
        log_path = work / "engine.log"
        with log_path.open("wb") as log_file:
            state.proc = subprocess.Popen(
                [dotnet, LAUNCHER, "--config", str(config)],  # fixed command line: no extra LEAN arguments
                cwd=install_dir / "bin",
                stdout=log_file,
                stderr=subprocess.STDOUT,
                env=env,  # Python strategies: LEAN's pythonnet uses QuantLab's Python 3.11
            )
        if on_started is not None:
            on_started(work)
        deadline = time.monotonic() + max_minutes * 60 if max_minutes is not None else None
        state.stopped_by = "algorithm"
        last_beat = 0.0
        while True:
            if time.monotonic() - last_beat >= HEARTBEAT_S:  # the console tells running from crashed by this
                last_beat = time.monotonic()
                try:
                    _write_status(state, work)
                except OSError as exc:  # e.g. Windows PermissionError while a reader has the file open
                    log.warning("deploy.status_write_failed", error=repr(exc))
            try:
                state.exit_code = state.proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                pass
            except KeyboardInterrupt:  # Ctrl+C: the owner stops the deployment (cleaned up below)
                state.stopped_by = "owner"
                break
            if stop.is_set():
                state.stopped_by = "owner"
                break
            if not state.thread.is_alive():  # no gateway: LEAN cannot trade; stop instead of running on
                state.stopped_by = "gateway failure"
                break
            if deadline is not None and time.monotonic() > deadline:
                state.stopped_by = "time limit"
                break
    # The cleanup above has run: the exit code is LEAN's real one, also after a terminate.
    return DeploymentResult(deployment, work, log_path, state.exit_code, state.stopped_by)


@dataclass
class _Running:
    gateway: Gateway | None = None
    thread: threading.Thread | None = None
    proc: subprocess.Popen[bytes] | None = None
    exit_code: int | None = None
    stopped_by: str = "error"
    started: bool = False
    actor: str = "owner"
    status: dict[str, object] | None = None


def _finish(
    state: _Running,
    run_file: Path,
    sessions: sessionmaker[Session],
    audit: AuditLog,
    deployment: str,
    work: Path,
) -> None:
    """Every step runs even if an earlier one fails or a second Ctrl+C arrives."""
    steps: list[tuple[str, Callable[[], None]]] = [
        ("stop LEAN", lambda: _stop_process(state)),
        ("stop gateway", lambda: state.gateway.stop() if state.gateway is not None else None),
        ("join gateway", lambda: state.thread.join(timeout=10) if state.thread is not None else None),
        ("remove token", lambda: run_file.unlink(missing_ok=True)),  # valid only while the deployment runs
        ("record provenance", lambda: _write_provenance(state, work)),
        ("audit stop", lambda: _audit_stop(state, sessions, audit, deployment, work)),
        ("final status", lambda: _write_status(state, work, final=True)),
    ]
    for name, step in steps:
        try:
            step()
        except BaseException as exc:  # including KeyboardInterrupt: the remaining steps still run
            log.warning("deploy.cleanup_failed", step=name, error=repr(exc))


def _write_status(state: _Running, work: Path, *, final: bool = False) -> None:
    """``status.json`` in the deployment's folder: what runs, since when, heartbeat, and how it ended."""
    if state.status is None:
        return
    status = {
        **state.status,
        "heartbeat_at": datetime.now(UTC).isoformat(),
        "data_states": _data_states(state),
    }
    if final:
        status.update(
            state="stopped",
            stopped_at=status["heartbeat_at"],
            stopped_by=state.stopped_by,
            exit_code=state.exit_code,
        )
    tmp = work / "status.json.tmp"
    tmp.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(work / "status.json")


def read_deployments(home: Path, now: datetime) -> list[dict[str, object]]:
    """Every deployment run under ``home`` (newest first) with ``running`` derived from the heartbeat."""
    out: list[dict[str, object]] = []
    root = home / "deployments"
    for status_file in sorted(root.glob("*/*/status.json"), reverse=True):
        try:
            data = json.loads(status_file.read_text(encoding="utf-8"))
            beat = datetime.fromisoformat(str(data.get("heartbeat_at")))
        except (OSError, ValueError, TypeError):
            continue
        age = (now - beat).total_seconds()
        running = data.get("state") == "running" and -5 <= age <= 3 * HEARTBEAT_S + 5  # small clock skew
        if data.get("state") == "running" and not running:
            data["state"] = "lost"  # no heartbeat: the process ended without cleaning up (crash, kill)
        data.update(run=status_file.parent.name, running=running, heartbeat_age_s=round(age, 1))
        out.append(data)
    return out


def _stop_process(state: _Running) -> None:
    proc = state.proc
    if proc is None:
        return
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
    state.exit_code = proc.returncode


def _data_states(state: _Running) -> list[str]:
    return sorted(state.gateway.data_states) if state.gateway is not None else []


def _write_provenance(state: _Running, work: Path) -> None:
    if not state.started:
        return
    states = _data_states(state)
    (work / "provenance.json").write_text(
        json.dumps(
            {
                "data_states": states,
                "synthetic": any(s != "LIVE" for s in states),  # SIMULATED / DELAYED / flagged quotes
                "note": "paper deployment: simulated executions, prices as labelled; not a performance claim",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def _audit_stop(
    state: _Running, sessions: sessionmaker[Session], audit: AuditLog, deployment: str, work: Path
) -> None:
    if not state.started:
        return
    with sessions() as session:
        audit.record(
            session, actor_type="user", actor=state.actor, action="paper.deploy.stop", target=deployment,
            details={"exit_code": state.exit_code, "stopped_by": state.stopped_by, "work_dir": str(work),
                     "data_states": _data_states(state)},
        )  # fmt: skip
        session.commit()


def cancel_deployment_orders(oms: OMS, sessions: sessionmaker[Session], deployment: str) -> dict[str, str]:
    """Cancel every working order a deployment placed (used by pause): order id -> new state or error."""
    from quantlab.oms import queries
    from quantlab.oms.service import CANCELLABLE

    with sessions() as session:
        ids = [
            o.id
            for o, st in queries.orders_with_state(session, oms.account, None)
            if o.source == f"lean:{deployment}" and st in CANCELLABLE
        ]
    out: dict[str, str] = {}
    for order_id in ids:
        try:
            out[order_id] = oms.cancel(order_id).value
        except Exception as exc:  # reported to the owner; the pause itself is already recorded
            out[order_id] = f"cancel failed: {exc}"
    return out
