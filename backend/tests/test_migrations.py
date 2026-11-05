"""Startup migrations on a populated SQLite database: a table rebuild must not take its children with it."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from vibonomics import migrations
from vibonomics.db import make_engine
from vibonomics.orm import (
    Balance,
    Base,
    EvalResult,
    EvalRun,
    Model,
    PriceHistory,
    ProviderConfig,
    UsageDay,
    UsageKey,
)

CHILDREN = ("models", "price_history", "balances", "usage_days", "usage_keys", "eval_results")

REBUILD = '''"""Rebuild the parent tables, as an alter_column or drop_column on them would."""

from alembic import op

revision = "aaaa00000001"
down_revision = "__HEAD__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("provider_configs", "models", "eval_runs"):
        with op.batch_alter_table(table, recreate="always"):
            pass


def downgrade() -> None:
    pass
'''

ORPHAN = '''"""Delete a parent row without its children, which a foreign key check must catch."""

from alembic import op

revision = "aaaa00000002"
down_revision = "__HEAD__"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM provider_configs WHERE id = 1")


def downgrade() -> None:
    pass
'''


@pytest.fixture
def engine(tmp_path: Path):
    engine = make_engine(tmp_path / "live.db")
    yield engine
    engine.dispose()


def _head() -> str:
    cfg = migrations._alembic_config()
    head = ScriptDirectory.from_config(cfg).get_current_head()
    assert head
    return head


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM {t}")).scalar_one() for t in ("provider_configs", *CHILDREN)
        }


def _populate(engine: Engine) -> None:
    with Session(engine) as s:
        provider = ProviderConfig(type="openrouter", name="Parent", enabled=True, options={})
        s.add(provider)
        s.flush()
        assert provider.id == 1
        model = Model(provider_config_id=provider.id, model_id="m", name="M")
        s.add(model)
        s.flush()
        s.add(PriceHistory(model_pk=model.id, price_input=1.0))
        s.add(Balance(provider_config_id=provider.id, status="ok"))
        s.add(UsageDay(provider_config_id=provider.id, date="2026-11-01", model="m"))
        s.add(UsageKey(provider_config_id=provider.id, key_hash="abc"))
        run = EvalRun(suite="quick")
        s.add(run)
        s.flush()
        s.add(
            EvalResult(
                run_id=run.id, provider_config_id=provider.id, model_id="m", task_id="t", category="c", status="passed"
            )
        )
        s.commit()


def _with_revision(tmp_path: Path, monkeypatch, source: str) -> None:
    """Point the runner at a copy of the migrations with one more revision on top of the real head."""
    copy = tmp_path / "migrations"
    shutil.copytree(migrations._HERE, copy, ignore=shutil.ignore_patterns("__pycache__"))
    (copy / "versions" / "zz_extra.py").write_text(source.replace("__HEAD__", _head()), encoding="utf-8")
    monkeypatch.setattr(migrations, "_HERE", copy)


def _foreign_keys_on(engine: Engine) -> bool:
    with engine.connect() as conn:
        return conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_there_is_one_migration_head():
    assert len(ScriptDirectory.from_config(migrations._alembic_config()).get_heads()) == 1


def test_the_migrations_build_what_the_models_declare_including_the_refresh_run_index(engine):
    migrations.upgrade_to_head(engine)

    with engine.connect() as conn:
        context = MigrationContext.configure(conn, opts={"compare_type": True, "render_as_batch": True})
        assert compare_metadata(context, Base.metadata) == []
        indexes = {row[1] for row in conn.exec_driver_sql("PRAGMA index_list(refresh_runs)")}
    assert "ix_refresh_runs_kind_id" in indexes


def test_a_batch_rebuild_of_a_parent_table_keeps_every_child_row(engine, tmp_path, monkeypatch):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    before = _counts(engine)
    assert all(before.values()), before

    _with_revision(tmp_path, monkeypatch, REBUILD)
    migrations.upgrade_to_head(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == "aaaa00000001"
        assert conn.exec_driver_sql("PRAGMA foreign_key_check").fetchall() == []
    assert _counts(engine) == before


def test_foreign_keys_are_on_again_after_a_migration_that_rebuilt_tables(engine, tmp_path, monkeypatch):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    _with_revision(tmp_path, monkeypatch, REBUILD)
    migrations.upgrade_to_head(engine)

    # The connection the migration used is back in the pool, so this is the one the app would get next.
    assert _foreign_keys_on(engine)
    with Session(engine) as s:
        s.add(Model(provider_config_id=999, model_id="orphan", name="Orphan"))
        with pytest.raises(Exception, match="FOREIGN KEY"):
            s.commit()


def test_startup_on_a_populated_database_that_is_already_current_changes_nothing(engine):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    before = _counts(engine)

    migrations.upgrade_to_head(engine)

    assert _counts(engine) == before
    assert _foreign_keys_on(engine)


def test_a_migration_that_orphans_rows_fails_loudly_and_is_rolled_back(engine, tmp_path, monkeypatch):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    before = _counts(engine)
    real_head = _head()
    _with_revision(tmp_path, monkeypatch, ORPHAN)

    with pytest.raises(RuntimeError, match="foreign key"):
        migrations.upgrade_to_head(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == real_head
    assert _counts(engine) == before
    assert _foreign_keys_on(engine)


def test_a_database_from_an_older_release_is_upgraded_with_its_rows_intact(engine):
    cfg = migrations._alembic_config()
    with migrations._foreign_keys_off(engine) as connection, connection.begin():
        cfg.attributes["connection"] = connection
        migrations.command.upgrade(cfg, "d46b879dc74e")
        connection.exec_driver_sql(
            "INSERT INTO provider_configs (type, name, enabled, options, created_at, updated_at) "
            "VALUES ('openrouter', 'Old', 1, '{}', '2026-10-01 00:00:00', '2026-10-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "INSERT INTO models (provider_config_id, model_id, name, input_modalities, output_modalities, is_free, "
            "pricing_source, extra, first_seen, last_seen, active, baseline) "
            "VALUES (1, 'm', 'M', '[]', '[]', 0, '', '{}', '2026-10-01 00:00:00', '2026-10-01 00:00:00', 1, 0)"
        )

    migrations.upgrade_to_head(engine)

    with engine.connect() as conn:
        assert conn.exec_driver_sql("SELECT version_num FROM alembic_version").scalar() == _head()
        assert conn.exec_driver_sql("SELECT count(*) FROM provider_configs").scalar() == 1
        assert conn.exec_driver_sql("SELECT count(*) FROM models").scalar() == 1
    assert _foreign_keys_on(engine)


def _orphan(engine: Engine) -> None:
    """A child row whose parent is gone, as a database edited by hand with enforcement off could hold."""
    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA foreign_keys=OFF")
        conn.exec_driver_sql(
            "INSERT INTO models (provider_config_id, model_id, name, input_modalities, output_modalities, is_free, "
            "pricing_source, extra, first_seen, last_seen, active, baseline) "
            "VALUES (999, 'orphan', 'Orphan', '[]', '[]', 0, '', '{}', '2026-10-01', '2026-10-01', 1, 0)"
        )
        conn.commit()
        conn.exec_driver_sql("PRAGMA foreign_keys=ON")


def test_a_stray_row_the_database_already_held_does_not_stop_a_migration(engine, tmp_path, monkeypatch):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    _orphan(engine)
    before = _counts(engine)
    _with_revision(tmp_path, monkeypatch, REBUILD)

    migrations.upgrade_to_head(engine)

    assert _counts(engine) == before


def test_a_migration_is_still_refused_for_orphaning_more_rows_than_the_database_held(engine, tmp_path, monkeypatch):
    migrations.upgrade_to_head(engine)
    _populate(engine)
    _orphan(engine)
    before = _counts(engine)
    _with_revision(tmp_path, monkeypatch, ORPHAN)

    with pytest.raises(RuntimeError, match="models -> provider_configs"):
        migrations.upgrade_to_head(engine)

    assert _counts(engine) == before
