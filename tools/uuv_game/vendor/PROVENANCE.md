# Dubins Kernel Provenance

- Project: AtsushiSakai/PythonRobotics, MIT license (adjacent LICENSE).
- Pinned commit: `b2020cd613d0709e9c0f38a7579f1a681cf7a227`.
- Retrieved: 2026-09-27.
- Source: https://github.com/AtsushiSakai/PythonRobotics/blob/b2020cd613d0709e9c0f38a7579f1a681cf7a227/PathPlanning/DubinsPath/dubins_path_planner.py
- Included: six analytic family formulas, trigonometric helper, family map.
- Adaptations: scalar modulo replaces NumPy angle helper; local `candidates`
  adapter returns all feasible families sorted by length in meters. Upstream
  plotting, path sampling, imports and CLI are omitted. Application sampling
  uses exact constant-curvature integration and a physical-meter step bound.
- No upstream collision, allocation, coverage or safety guarantees are claimed.
