from __future__ import annotations

import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_and_downgrade_baseline_sqlite(monkeypatch, tmp_path):
    root_dir = Path(__file__).resolve().parents[1]
    database_url = f"sqlite:///{(tmp_path / 'migration_test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    alembic_config = Config(str(root_dir / "alembic.ini"))
    engine = create_engine(database_url)

    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(engine)
        assert inspector.has_table("submissions")
        columns = {column["name"] for column in inspector.get_columns("submissions")}
        assert columns == {
            "id",
            "first_name",
            "last_name",
            "email",
            "form_type",
            "status",
            "created_at",
        }

        indexes = {index["name"] for index in inspector.get_indexes("submissions")}
        assert {"ix_submissions_email", "ix_submissions_id"}.issubset(indexes)

        command.downgrade(alembic_config, "base")

        inspector = inspect(engine)
        assert not inspector.has_table("submissions")
    finally:
        engine.dispose()


def test_alembic_revision_template_exists_and_generates_temp_revision(tmp_path):
    root_dir = Path(__file__).resolve().parents[1]
    template_path = root_dir / "migrations" / "script.py.mako"
    assert template_path.exists()

    temp_script_location = tmp_path / "migrations"
    temp_versions = temp_script_location / "versions"
    temp_versions.mkdir(parents=True)
    shutil.copyfile(template_path, temp_script_location / "script.py.mako")

    alembic_config = Config()
    alembic_config.set_main_option("script_location", str(temp_script_location))

    revision = command.revision(
        alembic_config,
        message="template smoke test",
        rev_id="template_smoke_test",
    )

    generated_path = Path(revision.path)
    assert generated_path.parent == temp_versions
    assert generated_path.exists()
