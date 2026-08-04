"""Admin Phase A — internal observability & data-governance BFF.

The browser talks only to this surface (`/admin/v1/...`); it aggregates the existing internal
service APIs into frontend-friendly read models. Primarily read-only. Server-side authentication and
role authorization are enforced on every route. All of it is gated by `ADMIN_API_ENABLED` (default off).
"""
