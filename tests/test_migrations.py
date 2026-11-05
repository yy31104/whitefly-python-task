from __future__ import annotations

import shutil
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from shared import models  # noqa: F401
from shared.db import Base


def _alembic_config() -> Config:
    root_dir = Path(__file__).resolve().parents[1]
    return Config(str(root_dir / "alembic.ini"))


def _column_names(engine, table_name: str = "submissions") -> set[str]:
    return {column["name"] for column in inspect(engine).get_columns(table_name)}


def _indexes(engine, table_name: str = "submissions") -> dict[str, dict]:
    return {index["name"]: index for index in inspect(engine).get_indexes(table_name)}


def _insert_submission(
    connection, *, email: str, idempotency_key: str | None = None
) -> None:
    connection.execute(
        text("""
            INSERT INTO submissions (
                first_name,
                last_name,
                email,
                form_type,
                status,
                created_at,
                idempotency_key
            )
            VALUES (
                :first_name,
                :last_name,
                :email,
                :form_type,
                :status,
                :created_at,
                :idempotency_key
            )
            """),
        {
            "first_name": "Ada",
            "last_name": "Lovelace",
            "email": email,
            "form_type": "async",
            "status": "processed",
            "created_at": "2026-05-29 00:00:00",
            "idempotency_key": idempotency_key,
        },
    )


def test_alembic_upgrade_and_downgrade_baseline_sqlite(monkeypatch, tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'migration_test.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    alembic_config = _alembic_config()
    engine = create_engine(database_url)

    try:
        command.upgrade(alembic_config, "20260529_0001")

        inspector = inspect(engine)
        assert inspector.has_table("submissions")
        assert _column_names(engine) == {
            "id",
            "first_name",
            "last_name",
            "email",
            "form_type",
            "status",
            "created_at",
        }
        assert "idempotency_key" not in _column_names(engine)

        indexes = set(_indexes(engine))
        assert {"ix_submissions_email", "ix_submissions_id"}.issubset(indexes)

        command.downgrade(alembic_config, "base")

        inspector = inspect(engine)
        assert not inspector.has_table("submissions")
    finally:
        engine.dispose()


def test_alembic_idempotency_key_schema_and_constraints_sqlite(monkeypatch, tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'idempotency_key_migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    alembic_config = _alembic_config()
    engine = create_engine(database_url)

    try:
        command.upgrade(alembic_config, "20260529_0001")
        assert "idempotency_key" not in _column_names(engine)

        command.upgrade(alembic_config, "head")
        assert "idempotency_key" in _column_names(engine)

        indexes = _indexes(engine)
        idempotency_index = indexes["ix_submissions_idempotency_key"]
        assert idempotency_index["column_names"] == ["idempotency_key"]
        assert idempotency_index["unique"]

        with engine.begin() as connection:
            _insert_submission(connection, email="null-1@example.com")
            _insert_submission(connection, email="null-2@example.com")
            _insert_submission(
                connection,
                email="distinct-1@example.com",
                idempotency_key="distinct-key-1",
            )
            _insert_submission(
                connection,
                email="distinct-2@example.com",
                idempotency_key="distinct-key-2",
            )
            _insert_submission(
                connection,
                email="duplicate-1@example.com",
                idempotency_key="duplicate-key",
            )
            try:
                _insert_submission(
                    connection,
                    email="duplicate-2@example.com",
                    idempotency_key="duplicate-key",
                )
            except IntegrityError:
                pass
            else:
                raise AssertionError("duplicate non-null idempotency_key was allowed")

        command.downgrade(alembic_config, "20260529_0001")
        assert "idempotency_key" not in _column_names(engine)
        assert "ix_submissions_idempotency_key" not in _indexes(engine)
    finally:
        engine.dispose()


def test_create_all_schema_matches_alembic_head_for_idempotency_key(
    monkeypatch, tmp_path
):
    alembic_database_url = f"sqlite:///{(tmp_path / 'alembic_head.db').as_posix()}"
    create_all_database_url = f"sqlite:///{(tmp_path / 'create_all.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", alembic_database_url)

    alembic_engine = create_engine(alembic_database_url)
    create_all_engine = create_engine(create_all_database_url)
    try:
        command.upgrade(_alembic_config(), "head")
        Base.metadata.create_all(bind=create_all_engine)

        assert _column_names(alembic_engine) == _column_names(create_all_engine)

        alembic_index = _indexes(alembic_engine)["ix_submissions_idempotency_key"]
        create_all_index = _indexes(create_all_engine)["ix_submissions_idempotency_key"]
        assert (
            alembic_index["column_names"]
            == create_all_index["column_names"]
            == ["idempotency_key"]
        )
        assert alembic_index["unique"]
        assert create_all_index["unique"]
    finally:
        alembic_engine.dispose()
        create_all_engine.dispose()


def test_alembic_task_status_schema_sqlite(monkeypatch, tmp_path):
    database_url = f"sqlite:///{(tmp_path / 'task_status_migration.db').as_posix()}"
    monkeypatch.setenv("DATABASE_URL", database_url)

    alembic_config = _alembic_config()
    engine = create_engine(database_url)

    try:
        command.upgrade(alembic_config, "head")

        inspector = inspect(engine)
        assert inspector.has_table("task_statuses")
        assert _column_names(engine, "task_statuses") == {
            "id",
            "task_id",
            "status",
            "submission_id",
            "error_type",
            "retries",
            "created_at",
            "updated_at",
        }

        task_status_indexes = _indexes(engine, "task_statuses")
        task_id_index = task_status_indexes["ix_task_statuses_task_id"]
        assert task_id_index["column_names"] == ["task_id"]
        assert task_id_index["unique"]

        command.downgrade(alembic_config, "20260529_0002")
        inspector = inspect(engine)
        assert not inspector.has_table("task_statuses")
    finally:
        engine.dispose()


def test_create_all_schema_matches_alembic_head_for_task_statuses(
    monkeypatch, tmp_path
):
    alembic_database_url = (
        f"sqlite:///{(tmp_path / 'alembic_task_status.db').as_posix()}"
    )
    create_all_database_url = (
        f"sqlite:///{(tmp_path / 'create_all_task_status.db').as_posix()}"
    )
    monkeypatch.setenv("DATABASE_URL", alembic_database_url)

    alembic_engine = create_engine(alembic_database_url)
    create_all_engine = create_engine(create_all_database_url)
    try:
        command.upgrade(_alembic_config(), "head")
        Base.metadata.create_all(bind=create_all_engine)

        assert _column_names(alembic_engine, "task_statuses") == _column_names(
            create_all_engine, "task_statuses"
        )

        alembic_index = _indexes(alembic_engine, "task_statuses")[
            "ix_task_statuses_task_id"
        ]
        create_all_index = _indexes(create_all_engine, "task_statuses")[
            "ix_task_statuses_task_id"
        ]
        assert (
            alembic_index["column_names"]
            == create_all_index["column_names"]
            == ["task_id"]
        )
        assert alembic_index["unique"]
        assert create_all_index["unique"]
    finally:
        alembic_engine.dispose()
        create_all_engine.dispose()


def test_create_all_schema_matches_alembic_head_for_idempotency_claims(
    monkeypatch, tmp_path
):
    alembic_database_url = f"sqlite:///{(tmp_path / 'alembic_claims.db').as_posix()}"
    create_all_database_url = (
        f"sqlite:///{(tmp_path / 'create_all_claims.db').as_posix()}"
    )
    monkeypatch.setenv("DATABASE_URL", alembic_database_url)

    alembic_engine = create_engine(alembic_database_url)
    create_all_engine = create_engine(create_all_database_url)
    try:
        command.upgrade(_alembic_config(), "head")
        Base.metadata.create_all(bind=create_all_engine)

        assert _column_names(alembic_engine, "idempotency_claims") == _column_names(
            create_all_engine, "idempotency_claims"
        )
        for engine in (alembic_engine, create_all_engine):
            primary_key = inspect(engine).get_pk_constraint("idempotency_claims")
            assert primary_key["constrained_columns"] == ["idempotency_key"]

        command.downgrade(_alembic_config(), "20260529_0003")
        assert "idempotency_claims" not in inspect(alembic_engine).get_table_names()
    finally:
        alembic_engine.dispose()
        create_all_engine.dispose()


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
