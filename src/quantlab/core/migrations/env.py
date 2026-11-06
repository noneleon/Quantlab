"""Alembic environment. Invoked via quantlab.core.db.migrate(); a connection is passed in."""

from alembic import context
from sqlalchemy import engine_from_config, pool

import quantlab.core.audit  # noqa: F401  (register models on Base.metadata)
from quantlab.core.db import Base

# Every module that defines tables must be imported above so autogenerate sees it.
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
