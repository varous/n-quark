"""Gateway-owned persistence (admin governance): SQLAlchemy models + engine/session.

Schema is Alembic-managed (version table ``alembic_version_gateway``); production never relies on
runtime ``create_all``. A clearly-isolated SQLite dev/test fallback creates tables from metadata so the
admin console works offline without a migration step.
"""
