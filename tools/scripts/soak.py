"""No model requests: a real wall-clock runtime soak with a checked search loop."""
import argparse
import json
from pathlib import Path
import resource
import tempfile
import time

from uuv_game.runtime import MissionRuntime


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=1800)
    parser.add_argument("--output", default="tools/artifacts/soak.json")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="uuv-soak-") as directory:
        path = Path(directory)/"mission.sqlite"
        runtime = MissionRuntime(path)
        runtime.targets = []  # Isolate long-term runtime behavior from encounter scenarios.
        runtime.set_mode("full")
        result = runtime.calculate("plan_search", {"members": ["UUV-1"], "bbox": [300, 300, 1700, 1700]})
        assert result["status"] == "succeeded", result
        runtime.submit(result["result_id"], "soak-start", runtime.episode)
        runtime.start()
        start = last = time.monotonic()
        last_save = last_report = start
        accumulator = 0.0
        restarted = False
        samples = []
        while time.monotonic()-start < args.seconds:
            now = time.monotonic()
            accumulator += (now-last)*runtime.config.simulation_speed
            last = now
            while accumulator >= runtime.config.dt:
                runtime.tick()
                accumulator -= runtime.config.dt
                assert runtime.status == "running", runtime.events[-1]
            if now-last_save >= 1:
                runtime.save()
                runtime.store.frame(runtime.episode, runtime.frame())
                last_save = now
            if now-start >= args.seconds/2 and not restarted:
                before = runtime.mission_state()
                runtime.close()
                runtime = MissionRuntime(path)
                assert runtime.mission_state()["uuvs"] == before["uuvs"]
                restarted = True
            if now-last_report >= 60:
                sample = {"wall_s": round(now-start, 2), "sim_s": runtime.sim_time,
                          "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                          "jobs": len(runtime.agent_jobs), "events": len(runtime.events),
                          "database_bytes": sum(p.stat().st_size for p in Path(directory).iterdir())}
                samples.append(sample)
                print(json.dumps(sample), flush=True)
                last_report = now
            time.sleep(.02)
        report = {"passed": True, "wall_s": time.monotonic()-start, "sim_s": runtime.sim_time,
                  "active_uuvs": len(runtime.active), "total_uuvs": len(runtime.uuvs),
                  "targets": 0, "model_connected": False, "checkpoint_restarted": restarted,
                  "search_loops": sum(event["type"] == "search_complete" for event in runtime.events),
                  "samples": samples}
        runtime.close()
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2))
        print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
