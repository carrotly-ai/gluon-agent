"""Per-domain FastAPI APIRouters extracted from the create_app closure (#162 STEP B).

Each router receives shared state (currently just the store) via FastAPI
``Depends`` reading ``request.app.state``, rather than closing over create_app
locals. Routers are included by ``create_app`` and stay behind the same
fail-closed auth middleware (route paths are unchanged).
"""
