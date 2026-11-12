"""Application entrypoint. The isolated financial proof has a separate Worker."""

from workers import asgi

from erp_cf.app import create_app
from erp_cf.limits_worker import LoginGuard as LoginGuard

app = create_app()
Default = asgi.entrypoint(app)
