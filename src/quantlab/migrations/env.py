"""Alembic environment. Invoked via quantlab.core.db.migrate(); a connection is passed in."""

from alembic import context
from sqlalchemy import engine_from_config, pool

from quantlab.backtest import models as backtest_models
from quantlab.core import audit
from quantlab.core.db import Base
from quantlab.data import catalog as data_models
from quantlab.strategies import registry as strategy_models

# Every module that defines tables must be listed here so its models are registered on Base.metadata
# (otherwise autogenerate would propose dropping their tables).
MODEL_MODULES = (audit, strategy_models, backtest_models, data_models)
target_metadata = Base.metadata
config = context.config


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()
        return
    engine = engine_from_config(
        config.get_section(config.config_ini_section) or {}, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with engine.begin() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    context.configure(url=config.get_main_option("sqlalchemy.url"), target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()
else:
    run_migrations()
