from __future__ import annotations

from alembic import context

from vibonomics.orm import Base

config = context.config
target_metadata = Base.metadata

connection = config.attributes.get("connection")
if connection is None:
    raise RuntimeError("Vibonomics runs migrations programmatically; see vibonomics/migrations/__init__.py")

context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,  # SQLite needs batch mode for ALTER TABLE
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
