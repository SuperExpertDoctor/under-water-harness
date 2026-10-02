"""Interface adapters and the process skeleton connecting the functional
modules.

This package is the wiring layer of the project:

- ``api`` — the FastAPI surface. Translates HTTP/WebSocket requests into
  ``uuv_game.runtime`` calls and runtime state back into protocol payloads;
  serves the plugin registry and the agent tool catalog to the outside.
- ``agent_events`` — projects PI agent session events (``agent/``) into the
  public event stream the API broadcasts.
- ``recording`` — drives the Playwright capture pipeline that renders the
  UI (``ui/``) into MP4 artifacts under ``outputs/``.
- ``launcher`` — boots every module as one supervised process set and
  mints the timestamped ``outputs/<YYYYMMDD-HHMMSS>/`` run directory each
  launch writes to (sqlite, tokens, logs, PI runtime, recordings).

State machines — mission/contacts/plans/approval-mode transitions — are
algorithm-internal and live in ``tools/uuv_game/runtime.py`` (and the
capability modules it calls), not here. ``api`` reads them through the
runtime's public surface; the contract that boundary relies on is
documented in ``tools/uuv_game/plugins/contract.py``.
"""
