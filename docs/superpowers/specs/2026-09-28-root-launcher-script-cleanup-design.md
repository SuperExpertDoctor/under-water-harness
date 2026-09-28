# Root Launcher and Script Cleanup

## Scope

The repository root exposes `./run.sh` as the single operator entry point. It accepts the existing launcher arguments (`--status`, `--stop`, `--no-model`, `--foreground`, `--port`, `--ui-port`) and works from another working directory. Prefer `tools/.venv/bin/python` when available; otherwise use `python3`. It does not install dependencies or implicitly stop/restart an existing mission.

The Python process supervisor moves from `tools/scripts/run.py` to `tools/launcher.py`. Its runtime database, logs, credentials, and service metadata remain in `tools/.runtime`. The new supervisor recognizes a still-running supervisor started at the old script path so it cannot accidentally start a second service against the same database; `--stop` remains explicit. Existing root `scripts/` belongs to the monorepo and is not modified.

Keep the nine intentional acceptance drivers under `tools/acceptance/`: `model_acceptance.py`, `soak.py`, `v2_acceptance.py`, `v2_browser_acceptance.py`, `v2_live_acceptance.py`, `v2_live_soak.py`, `v2_moving_replan.py`, `v3_browser_acceptance.py`, and `v4_interaction_acceptance.py`. Move the asset derivative generator to `tools/prepare_submarine_asset.py`. Update direct imports, path-relative repository resolution, active documentation commands, and launcher/acceptance/asset tests.

Remove exactly five unused one-off probes: `approval_inline_smoke.py`, `isolated_longcat_smoke.py`, `ui_smoke_playwright.py`, `v3_runtime_probe.py`, and `v4_information_probe.py`. They are recoverable from Git history. Do not delete any generated output or affect running services. Historical acceptance reports stay immutable; update current instructions and tests, not their recorded observations.

## Verification

Add a regression test for root `run.sh` dispatch and for recognizing old/new supervisor paths. Make the existing launcher, asset, and offline acceptance tests import from their new locations. Check `./run.sh --help` and `./run.sh --status` without triggering a restart. Run modified tests and `npm run check`, with no full build or `npm test`.
