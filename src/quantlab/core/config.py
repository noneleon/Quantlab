"""Layered configuration (CORE-002). Reference: docs/CONFIGURATION_REFERENCE.md.

Precedence, lowest to highest:
    built-in defaults < QUANTLAB_HOME/quantlab.toml < QUANTLAB_* environment variables < explicit overrides

Environment variables use ``__`` for nesting, e.g. ``QUANTLAB_SERVER__PORT=9000``.

Safety properties enforced here (not left to callers):
* Unknown keys are rejected, so a typo or a pasted secret is not silently accepted.
* The API cannot bind to a non-loopback address unless ``server.remote_access`` is true.
* ``trading.mode = "live"`` is rejected until the live safety gates (LIVE-003) exist.
* The LEAN Docker image must be pinned by digest for reproducibility (DEC-0004).
* Secrets never live in configuration; only ``secret://`` references are allowed (SECURITY_POLICY.md).
"""

from __future__ import annotations

import ipaddress
import tomllib
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

from quantlab.core.paths import HomeLayout, default_home

ENV_PREFIX = "QUANTLAB_"
SECRET_REF_PREFIX = "secret://"  # noqa: S105 - URI scheme, not a secret


class ConfigError(ValueError):
    """Raised when configuration cannot be loaded or is invalid."""


class _Section(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _is_loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


class ServerSettings(_Section):
    host: str = "127.0.0.1"
    port: int = Field(default=8765, ge=1, le=65535)
    remote_access: bool = False
    ui_dir: Path | None = None  # built web UI (index.html + assets/); None -> web/dist of a source checkout

    @model_validator(mode="after")
    def _local_only_unless_enabled(self) -> ServerSettings:
        if not _is_loopback(self.host) and not self.remote_access:
            raise ValueError(
                f"server.host={self.host!r} is not a loopback address; "
                "set server.remote_access=true explicitly to expose the API (see SECURITY_POLICY.md)"
            )
        return self


class DatabaseSettings(_Section):
    url: str | None = None  # None -> SQLite file under QUANTLAB_HOME/db


class LeanSettings(_Section):
    launcher: Literal["docker", "native"] = "docker"
    image: str | None = None  # e.g. "quantconnect/lean@sha256:<64 hex>"; required before a docker run
    native_path: Path | None = None
    docker_command: tuple[str, ...] = ("docker",)  # e.g. ("podman",); executed without a shell
    network: Literal["none", "bridge"] = "none"  # backtests run without network access by default
    timeout_s: int = Field(default=3600, ge=1)  # a backtest exceeding this is stopped

    @field_validator("docker_command")
    @classmethod
    def _non_empty_command(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if not value or not all(part.strip() for part in value):
            raise ValueError("engine.lean.docker_command must be a non-empty list of non-empty strings")
        return value

    @field_validator("image")
    @classmethod
    def _pinned_by_digest(cls, value: str | None) -> str | None:
        if value is None:
            return value
        _, sep, digest = value.partition("@sha256:")
        if not sep or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError("engine.lean.image must be pinned by digest: '<repo>@sha256:<64 lowercase hex>'")
        return value


class EngineSettings(_Section):
    default: Literal["lean"] = "lean"
    lean: LeanSettings = LeanSettings()


class DataSettings(_Section):
    root: Path | None = None  # None -> QUANTLAB_HOME/data


class TradingSettings(_Section):
    mode: Literal["paper", "live"] = "paper"

    @field_validator("mode")
    @classmethod
    def _live_not_available(cls, value: str) -> str:
        if value == "live":
            raise ValueError(
                "trading.mode='live' is not available: live trading requires the LIVE-003 safety gates "
                "(not implemented) and explicit owner activation through the UI/CLI, never config alone"
            )
        return value


class IbkrSettings(_Section):
    host: str = "127.0.0.1"
    port_paper: int = Field(default=4002, ge=1, le=65535)
    port_live: int = Field(default=4001, ge=1, le=65535)
    client_id: int = Field(default=17, ge=0)  # broker (orders) connection — Phase 5
    market_data_client_id: int = Field(default=18, ge=0)  # read-only market-data connection (DATA-019)

    @model_validator(mode="after")
    def _distinct_client_ids(self) -> IbkrSettings:
        if self.client_id == self.market_data_client_id:
            raise ValueError("brokers.ibkr.client_id and market_data_client_id must differ")
        return self


class BrokersSettings(_Section):
    ibkr: IbkrSettings = IbkrSettings()


class RiskSettings(_Section):
    limits_file: Path | None = None  # None -> QUANTLAB_HOME/risk_limits.toml


class MarketDataSettings(_Section):
    """Provider selection (DEC-0021). Strategies never see which provider is active."""

    historical: Literal["tiingo", "ibkr"] = "tiingo"  # source for `quantlab data fetch`
    realtime: Literal["mock", "ibkr"] = "mock"  # source for real-time quotes
    require_live: bool = False  # reject anything but LIVE quotes (live trading always requires LIVE)
    mock_dataset: str | None = None  # catalog dataset replayed by the mock real-time provider
    stale_after_s: float = Field(default=15.0, gt=0)
    # Market data type requested from IBKR. Whatever IB actually delivers is reported per quote.
    ibkr_data_type: Literal["live", "frozen", "delayed", "delayed_frozen"] = "live"

    @model_validator(mode="after")
    def _mock_cannot_be_live(self) -> MarketDataSettings:
        if self.require_live and self.realtime == "mock":
            raise ValueError(
                "market_data.require_live=true is impossible with realtime='mock' (mock data is SIMULATED)"
            )
        if self.require_live and self.ibkr_data_type != "live":
            raise ValueError("market_data.require_live=true requires ibkr_data_type='live'")
        return self


class ExecutionModelSettings(_Section):
    """Backtest execution models applied by ``quantlab_lean.configure`` (ENG-009, BT-005..007, DEC-0023).

    ``"brokerage"`` means: keep what LEAN's brokerage model assigns (LEAN's own default). Every value is
    recorded in the run manifest and is part of the inputs hash.
    """

    brokerage: Literal["default", "interactive_brokers"] = "default"
    account_type: Literal["margin", "cash"] = "margin"
    fee: Literal["brokerage", "zero", "constant"] = "brokerage"
    fee_per_order: float = Field(default=0.0, ge=0)  # account currency, with fee = "constant"
    slippage: Literal["brokerage", "none", "constant", "volume_share"] = "brokerage"
    # fraction of price, with slippage = "constant"
    slippage_percent: float = Field(default=0.0, ge=0, lt=0.1)
    volume_limit: float = Field(default=0.025, gt=0, le=1)  # volume_share: max share of bar volume
    price_impact: float = Field(default=0.1, ge=0)  # volume_share: impact coefficient
    fill: Literal["brokerage", "immediate"] = "brokerage"
    settlement: Literal["brokerage", "immediate", "delayed"] = "brokerage"
    settlement_days: int = Field(default=1, ge=0, le=10)  # with settlement = "delayed"
    settlement_hour: int = Field(default=6, ge=0, le=23)  # time of day funds settle (exchange time)

    @model_validator(mode="after")
    def _consistent(self) -> ExecutionModelSettings:
        if self.fee == "constant" and self.fee_per_order <= 0:
            raise ValueError(
                "backtest.models.fee='constant' needs fee_per_order > 0 (use fee='zero' for none)"
            )
        if self.slippage == "constant" and self.slippage_percent <= 0:
            raise ValueError(
                "backtest.models.slippage='constant' needs slippage_percent > 0 (use slippage='none')"
            )
        return self


class PaperSettings(_Section):
    """Paper trading: the local simulated broker (BRK-002) or an IBKR paper account (BRK-003, DEC-0029)."""

    broker: Literal["local", "ibkr"] = "local"
    account: str = "paper"
    starting_cash: float = Field(default=100_000.0, gt=0)
    commission_per_share: float = Field(default=0.005, ge=0)
    min_commission: float = Field(default=1.0, ge=0)
    slippage_bps: float = Field(default=1.0, ge=0)
    allow_short: bool = False
    # Strategy processes of `paper deploy` (DEC-0035): "auto" isolates on Linux/WSL2 (refuses if that
    # fails) and warns elsewhere; "required" refuses without isolation; "off" never isolates (owner's choice).
    deployment_isolation: Literal["auto", "required", "off"] = "auto"
    ibkr_account: str | None = None  # the IBKR paper account id (DU...), required with broker = "ibkr"
    ibkr_starting_cash: float | None = Field(
        default=None, gt=0
    )  # that account's cash when QuantLab starts using it

    @property
    def oms_account(self) -> str:
        """OMS account name of the selected paper broker (orders and fills are kept per account)."""
        return f"ibkr-{self.ibkr_account}" if self.broker == "ibkr" else self.account

    @property
    def oms_starting_cash(self) -> float:
        if self.broker == "ibkr":
            assert self.ibkr_starting_cash is not None  # validated
            return self.ibkr_starting_cash
        return self.starting_cash

    @model_validator(mode="after")
    def _ibkr_needs_account(self) -> PaperSettings:
        if self.broker == "ibkr":
            if not self.ibkr_account or not self.ibkr_account.startswith("DU"):
                raise ValueError(
                    "paper.broker = 'ibkr' needs paper.ibkr_account = your IBKR paper account id (DU...)"
                )
            if self.ibkr_starting_cash is None:
                raise ValueError(
                    "paper.broker = 'ibkr' needs paper.ibkr_starting_cash "
                    "(the paper account's cash when you start)"
                )
        return self


class SupervisorSettings(_Section):
    """Paper trading supervisor (LIVE-001). It never sends orders and never disengages the kill switch."""

    interval_s: float = Field(default=5.0, ge=0.5)  # broker events are processed every tick
    reconcile_every_s: float = Field(default=60.0, ge=5)
    equity_every_s: float = Field(default=60.0, ge=5)  # equity samples (drawdown high-water mark)
    calendar: str = (
        "XNYS"  # session open (start-of-day equity) and close (DAY orders expire); "none" disables
    )
    engage_kill_switch_on_mismatch: bool = (
        True  # reconcile finds a position difference -> engage (risk-reducing)
    )
    equity_quote_max_age_s: float = Field(default=60.0, gt=0)  # older quotes never feed equity samples


class LifecycleSettings(_Section):
    """Promotion gates (STR-004, DEC-0026). Changing them is an owner decision; each event records them."""

    require_real_data: bool = True  # evidence runs must use real_historical / live_recorded data
    min_oos_windows: int = Field(default=3, ge=1)
    min_oos_sharpe: float = 0.5
    max_oos_drawdown: float = Field(default=0.30, gt=0, le=1)
    require_overfitting_checks: bool = True  # validation also needs a sweep with DSR / PBO within limits
    min_dsr_probability: float = Field(default=0.90, ge=0, le=1)
    max_pbo: float = Field(default=0.50, ge=0, le=1)


class BacktestSettings(_Section):
    models: ExecutionModelSettings = ExecutionModelSettings()


class AiSettings(_Section):
    provider: Literal["none", "anthropic", "openai", "ollama"] = "none"
    api_key_ref: str | None = None  # must be a secret:// reference, never the key itself
    ollama_url: str = "http://127.0.0.1:11434"

    @field_validator("api_key_ref")
    @classmethod
    def _must_be_reference(cls, value: str | None) -> str | None:
        if value is not None and not value.startswith(SECRET_REF_PREFIX):
            raise ValueError(
                f"ai.api_key_ref must be a '{SECRET_REF_PREFIX}<name>' reference; "
                "never put API keys in configuration"
            )
        return value


class LoggingSettings(_Section):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = "INFO"
    json_output: bool = True
    to_file: bool = True


# Path of the TOML file used by the settings source currently being built (set by load_settings).
_toml_path: ContextVar[Path | None] = ContextVar("quantlab_toml_path", default=None)


class _TomlSource(PydanticBaseSettingsSource):
    """Reads QUANTLAB_HOME/quantlab.toml. Missing file -> empty; malformed file -> ConfigError."""

    def __init__(self, settings_cls: type[BaseSettings]) -> None:
        super().__init__(settings_cls)
        path = _toml_path.get()
        self._data: dict[str, Any] = {}
        if path is not None and path.is_file():
            try:
                self._data = tomllib.loads(path.read_text(encoding="utf-8"))
            except tomllib.TOMLDecodeError as exc:
                raise ConfigError(f"invalid TOML in {path}: {exc}") from exc

    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:  # pragma: no cover
        return self._data.get(field_name), field_name, False

    def __call__(self) -> dict[str, Any]:
        return dict(self._data)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix=ENV_PREFIX,
        env_nested_delimiter="__",
        extra="forbid",
        frozen=True,
    )

    home: Path
    server: ServerSettings = ServerSettings()
    db: DatabaseSettings = DatabaseSettings()
    engine: EngineSettings = EngineSettings()
    data: DataSettings = DataSettings()
    trading: TradingSettings = TradingSettings()
    brokers: BrokersSettings = BrokersSettings()
    risk: RiskSettings = RiskSettings()
    market_data: MarketDataSettings = MarketDataSettings()
    backtest: BacktestSettings = BacktestSettings()
    lifecycle: LifecycleSettings = LifecycleSettings()
    paper: PaperSettings = PaperSettings()
    supervisor: SupervisorSettings = SupervisorSettings()
    ai: AiSettings = AiSettings()
    logging: LoggingSettings = LoggingSettings()

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # First source wins: overrides > env > toml > defaults. Dotenv/secret files deliberately unused.
        return (init_settings, env_settings, _TomlSource(settings_cls))

    # ---- derived values -------------------------------------------------------------------------
    @property
    def layout(self) -> HomeLayout:
        return HomeLayout(self.home)

    @property
    def database_url(self) -> str:
        if self.db.url:
            return self.db.url
        return f"sqlite:///{(self.layout.db_dir / 'quantlab.sqlite').as_posix()}"

    @property
    def data_root(self) -> Path:
        return self.data.root if self.data.root is not None else self.layout.data_dir

    @property
    def risk_limits_file(self) -> Path:
        return self.risk.limits_file if self.risk.limits_file is not None else self.home / "risk_limits.toml"

    @property
    def lean_native_dir(self) -> Path:
        """Native LEAN install (ENG-003): ``engine.lean.native_path`` or QUANTLAB_HOME/engine/lean-native."""
        configured = self.engine.lean.native_path
        return configured if configured is not None else self.home / "engine" / "lean-native"


@contextmanager
def _toml_from(path: Path) -> Iterator[None]:
    token = _toml_path.set(path)
    try:
        yield
    finally:
        _toml_path.reset(token)


def load_settings(home: Path | str | None = None, overrides: Mapping[str, Any] | None = None) -> Settings:
    """Load settings for a QuantLab home. Does not create any files or directories."""
    root = (Path(home).expanduser() if home is not None else default_home()).resolve()
    kwargs: dict[str, Any] = dict(overrides or {})
    kwargs["home"] = root
    try:
        with _toml_from(HomeLayout(root).config_file):
            return Settings(**kwargs)
    except ConfigError:
        raise
    except ValueError as exc:  # pydantic.ValidationError subclasses ValueError
        raise ConfigError(str(exc)) from exc


__all__ = [
    "AiSettings",
    "ConfigError",
    "LoggingSettings",
    "Settings",
    "load_settings",
]
