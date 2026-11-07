"""LEAN Docker launcher and version probe (ENG-002, ENG-006).

Runs the official LEAN image, pinned by digest (DEC-0004), with:

* the strategy directory mounted read-only at ``/LeanCLI`` (Lean CLI layout),
* a dataset export mounted read-only at ``/QuantLab/dataset`` and overlaid onto ``/Lean/Data`` inside the
  container after all bundled sample price data is removed (or, without a dataset, the sample data as is),
* per-run ``/Results`` and ``/Storage`` folders under the run's work dir,
* the generated ``config.json`` mounted read-only over the launcher's default,
* networking disabled by default — a backtest must not depend on, or leak to, the network.

Docker is invoked through ``docker_command`` (default ``("docker",)``) without a shell, so tests can
substitute a fake executable and the user can point at e.g. ``podman``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from quantlab.core.logging import get_logger
from quantlab.engine.base import (
    BacktestSpec,
    EngineError,
    EngineInfo,
    EngineResult,
    RunHandle,
    RunOutcome,
    RunState,
)
from quantlab.engine.lean.config import (
    CONTAINER_ALGORITHM,
    CONTAINER_CONFIG,
    CONTAINER_DATASET,
    CONTAINER_LIBRARY,
    CONTAINER_RESULTS,
    CONTAINER_STORAGE,
    DATASET_ENTRYPOINT_SCRIPT,
    build_backtest_config,
    write_config,
)
from quantlab.engine.lean.results import parse_results_dir

log = get_logger(__name__)

NETWORK_MODES = ("none", "bridge")


def _require_digest(image: str) -> None:
    _, sep, digest = image.partition("@sha256:")
    if not sep or len(digest) != 64:
        raise EngineError(
            f"LEAN image must be pinned by digest for reproducibility, got {image!r}; "
            "set engine.lean.image = '<repo>@sha256:<digest>'"
        )


def _mount(source: Path, target: str, *, readonly: bool) -> list[str]:
    src = str(source.resolve())
    if "," in src or '"' in src:
        raise EngineError(f"path not supported in a Docker --mount (contains ',' or '\"'): {src}")
    spec = f"type=bind,source={src},target={target}"
    return ["--mount", spec + (",readonly" if readonly else "")]


LIBRARY_SOURCE = Path(__file__).parent / "library" / "quantlab_lean"
MODELS_MARKER = "QUANTLAB_MODELS_APPLIED"


def models_hash(models: Mapping[str, Any]) -> str:
    return hashlib.sha256(json.dumps(dict(models), sort_keys=True).encode()).hexdigest()


def prepare_library(target: Path, models: Mapping[str, Any]) -> str:
    """Copy the quantlab_lean helper and write models.json (read-only mount for the container)."""
    package = target / "quantlab_lean"
    package.mkdir(parents=True)
    for source in sorted(LIBRARY_SOURCE.glob("*.py")):
        shutil.copy2(source, package / source.name)
    sha = models_hash(models)
    (package / "models.json").write_text(
        json.dumps({"sha256": sha, "models": dict(models)}, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return sha


def models_confirmed(handle: RunHandle, sha: str) -> bool:
    """Did the algorithm log the marker for exactly these models? (results log files and the engine log)."""
    needle = f"{MODELS_MARKER} sha256={sha}"
    candidates = [*sorted(handle.results_dir.glob("*log*.txt")), handle.log_path]
    for path in candidates:
        if path.is_file() and needle in path.read_text(encoding="utf-8", errors="replace"):
            return True
    return False


def container_name(run_id: str) -> str:
    return f"quantlab-{run_id.lower()}"


def run_layout(spec: BacktestSpec) -> RunHandle:
    work = spec.work_dir.resolve()
    return RunHandle(
        run_id=spec.run_id,
        work_dir=work,
        results_dir=work / "results",
        log_path=work / "engine.log",
        config_path=work / "lean-config.json",
        container_name=container_name(spec.run_id),
    )


def build_run_argv(
    spec: BacktestSpec,
    handle: RunHandle,
    image: str,
    docker_command: Sequence[str] = ("docker",),
    network: str = "none",
) -> list[str]:
    """Return the full ``docker run`` command line (no shell) for a prepared run."""
    if network not in NETWORK_MODES:
        raise EngineError(f"network must be one of {NETWORK_MODES}, got {network!r}")
    argv = [
        *docker_command,
        "run",
        "--rm",
        "--name",
        handle.container_name or container_name(spec.run_id),
        "--label",
        f"quantlab.run_id={spec.run_id}",
        "--network",
        network,
        *_mount(spec.algorithm.directory, CONTAINER_ALGORITHM, readonly=True),
        *_mount(handle.results_dir, CONTAINER_RESULTS, readonly=False),
        *_mount(handle.work_dir / "storage", CONTAINER_STORAGE, readonly=False),
        *_mount(handle.config_path, CONTAINER_CONFIG, readonly=True),
    ]
    if spec.models is not None:
        argv += _mount(handle.work_dir / "library", CONTAINER_LIBRARY, readonly=True)
    if spec.data_dir is None:
        argv.append(image)  # image entrypoint; LEAN's bundled sample data (provenance "sample")
    else:
        argv += _mount(spec.data_dir, CONTAINER_DATASET, readonly=True)
        argv += ["--entrypoint", "sh", image, "-c", DATASET_ENTRYPOINT_SCRIPT]
    return argv


class LeanDockerAdapter:
    """EngineAdapter implementation running LEAN in Docker."""

    name = "lean"
    launcher = "docker"

    def __init__(
        self,
        image: str,
        docker_command: Sequence[str] = ("docker",),
        network: str = "none",
        stop_timeout_s: int = 10,
        default_timeout_s: float | None = None,
    ) -> None:
        _require_digest(image)
        if network not in NETWORK_MODES:
            raise EngineError(f"network must be one of {NETWORK_MODES}, got {network!r}")
        self.image = image
        self.docker_command = tuple(docker_command)
        self.network = network
        self.stop_timeout_s = stop_timeout_s
        self.default_timeout_s = default_timeout_s
        self._procs: dict[str, subprocess.Popen[bytes]] = {}
        self._started: dict[str, float] = {}
        self._cancelled: set[str] = set()
        self._lock = threading.Lock()

    # ---- helpers ------------------------------------------------------------------------------
    def _docker(self, *args: str, timeout: float = 60) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [*self.docker_command, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise EngineError(f"docker executable not found: {self.docker_command[0]!r}") from exc
        except subprocess.TimeoutExpired as exc:
            raise EngineError(f"docker {' '.join(args[:2])} timed out after {timeout}s") from exc

    # ---- EngineAdapter -----------------------------------------------------------------------
    def probe(self) -> EngineInfo:
        server = self._docker("version", "--format", "{{.Server.Version}}")
        if server.returncode != 0 or not server.stdout.strip():
            raise EngineError(
                "Docker daemon not reachable (start Docker Desktop / dockerd): "
                + (server.stderr.strip() or server.stdout.strip())[:300]
            )
        inspect = self._docker("image", "inspect", "--format", "{{json .}}", self.image)
        if inspect.returncode != 0:
            raise EngineError(f"LEAN image not present locally; pull it first: docker pull {self.image}")
        try:
            meta = json.loads(inspect.stdout)
        except json.JSONDecodeError as exc:
            raise EngineError("could not parse `docker image inspect` output") from exc
        return EngineInfo(
            name=self.name,
            launcher=self.launcher,
            image=self.image,
            image_id=meta.get("Id"),
            repo_digests=tuple(meta.get("RepoDigests") or ()),
            created=meta.get("Created"),
            engine_runtime_version=f"docker {server.stdout.strip()}",
        )

    def pull(self) -> None:
        """Explicitly download the pinned image (never done implicitly during a run)."""
        result = self._docker("pull", self.image, timeout=3600)
        if result.returncode != 0:
            raise EngineError(f"docker pull failed: {result.stderr.strip()[:300]}")

    def start_backtest(self, spec: BacktestSpec) -> RunHandle:
        if not spec.algorithm.entry_path.is_file():
            raise EngineError(f"algorithm entry file not found: {spec.algorithm.entry_path}")
        if spec.data_dir is not None and not spec.data_dir.is_dir():
            raise EngineError(f"data directory not found: {spec.data_dir}")
        handle = run_layout(spec)
        if handle.work_dir.exists() and any(handle.work_dir.iterdir()):
            # Runs are immutable: never reuse or overwrite an existing run directory.
            raise EngineError(f"run directory already exists and is not empty: {handle.work_dir}")
        handle.results_dir.mkdir(parents=True, exist_ok=True)
        (handle.work_dir / "storage").mkdir(parents=True, exist_ok=True)
        if spec.models is not None:
            prepare_library(handle.work_dir / "library", spec.models)
        write_config(build_backtest_config(spec), handle.config_path)
        argv = build_run_argv(spec, handle, self.image, self.docker_command, self.network)
        (handle.work_dir / "docker-command.json").write_text(
            json.dumps(argv, indent=2) + "\n", encoding="utf-8"
        )

        log_file = handle.log_path.open("wb")
        try:
            proc = subprocess.Popen(argv, stdout=log_file, stderr=subprocess.STDOUT)
        except FileNotFoundError as exc:
            log_file.close()
            raise EngineError(f"docker executable not found: {self.docker_command[0]!r}") from exc
        finally:
            if not log_file.closed:
                log_file.close()  # child holds its own handle
        with self._lock:
            self._procs[spec.run_id] = proc
            self._started[spec.run_id] = time.monotonic()
        log.info("engine.started", run_id=spec.run_id, container=handle.container_name, image=self.image)
        return handle

    def wait(self, handle: RunHandle, timeout_s: float | None = None) -> RunOutcome:
        with self._lock:
            proc = self._procs.get(handle.run_id)
            started = self._started.get(handle.run_id, time.monotonic())
        if proc is None:
            raise EngineError(f"unknown run {handle.run_id} (not started by this adapter)")
        if timeout_s is None:
            timeout_s = self.default_timeout_s
        try:
            code = proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            self._stop(handle)
            proc.wait(timeout=self.stop_timeout_s + 30)
            outcome = RunOutcome(RunState.TIMED_OUT, proc.returncode, time.monotonic() - started,
                                 f"exceeded {timeout_s}s; container stopped")  # fmt: skip
        else:
            duration = time.monotonic() - started
            if handle.run_id in self._cancelled:
                outcome = RunOutcome(RunState.CANCELLED, code, duration, "cancelled by user")
            elif code == 0:
                outcome = RunOutcome(RunState.SUCCEEDED, code, duration)
            else:
                outcome = RunOutcome(RunState.FAILED, code, duration, f"engine exited with code {code}")
        with self._lock:
            self._procs.pop(handle.run_id, None)
        log.info("engine.finished", run_id=handle.run_id, state=outcome.state, exit_code=outcome.exit_code)
        return outcome

    def _stop(self, handle: RunHandle) -> None:
        name = handle.container_name or container_name(handle.run_id)
        self._docker("stop", "-t", str(self.stop_timeout_s), name, timeout=self.stop_timeout_s + 30)

    def cancel(self, handle: RunHandle) -> None:
        with self._lock:
            self._cancelled.add(handle.run_id)
        self._stop(handle)

    def collect(self, handle: RunHandle) -> EngineResult:
        result = parse_results_dir(handle.results_dir, handle.run_id)
        models_file = handle.work_dir / "library" / "quantlab_lean" / "models.json"
        if not models_file.is_file():
            return result
        sha = json.loads(models_file.read_text(encoding="utf-8"))["sha256"]
        return replace(result, models_applied=models_confirmed(handle, sha))
