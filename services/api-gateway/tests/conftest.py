"""Bind the gateway admin DB to a throwaway SQLite file before api_gateway.config is imported."""

import os
import tempfile

_TMP = tempfile.mkdtemp()
os.environ["NQUARK_ADMIN_AUDIT_DB_URL"] = f"sqlite:///{_TMP}/gateway_admin.db"
os.environ["NQUARK_NETWORK_MODE"] = "local"
