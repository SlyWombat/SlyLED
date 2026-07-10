"""orch_state — shared-state bridge for the parent_server blueprint split (B1).

parent_server.py owns ALL orchestrator module state (`_children`, `_fixtures`,
`_layout`, `_settings`, `_lock`, `_save`, `_load`, the UDP helpers, …). That
ownership is load-bearing: 100+ tests import `parent_server` and read *and
monkeypatch* those names directly (`parent_server._FW_DIR = tmp`, …), so the
state cannot move without breaking the world.

Instead, sections extracted from parent_server.py into Blueprint modules reach
back into parent_server through this bridge:

    import orch_state
    ps = orch_state.ps          # the parent_server module object
    ...
    with ps._lock:
        ps._save("children", ps._children)

parent_server calls ``orch_state.bind(sys.modules[__name__])`` immediately
after creating the Flask app — i.e. *before* any extracted module is imported —
so ``orch_state.ps`` is always populated by the time an extracted module's
top-level code runs. Binding the module object (rather than copying names)
means rebinds are visible in both directions: a route in an extracted module
that does ``ps._fixtures = new`` updates the exact attribute tests and the
rest of parent_server read, and a test that patches
``parent_server._resolve_registry`` is seen by extracted callers that go
through ``ps._resolve_registry``.

Why not import parent_server from the extracted modules? Because parent_server
is also run as a script (``python parent_server.py`` → module name
``__main__``); a plain ``import parent_server`` from an extracted module would
then execute the whole 20k-line file a *second* time (second UDP listener,
second state copy). ``bind()`` hands over whichever module object is actually
running, so both launch modes share one state.
"""

ps = None  # the live parent_server module; set exactly once by bind()


def bind(module):
    """Called by parent_server right after `app` is created."""
    global ps
    ps = module
    return module
