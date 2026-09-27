"""Browser evidence against existing services; optional explicit start/pause motion probe."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import time

import httpx
from playwright.sync_api import sync_playwright


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def canvas_evidence(page):
    return page.locator("canvas[aria-label='Operational map']").evaluate("""canvas => {
        const pixels = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data;
        const colors = new Set(); let opaque = 0;
        for (let i = 0; i < pixels.length; i += 64) {
            colors.add(`${pixels[i]},${pixels[i+1]},${pixels[i+2]},${pixels[i+3]}`);
            if (pixels[i+3] > 0) opaque++;
        }
        const bounds = canvas.getBoundingClientRect();
        return {width: canvas.width, height: canvas.height, sampled_colors: colors.size,
            opaque_samples: opaque, bounds: {x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height}};
    }""")


def state_summary(state):
    return {key: state.get(key) for key in ("episode_id", "frame_id", "runtime_status", "sim_time_min",
        "mission_metrics", "contacts", "bearing_lines")}


def capture(page, output, name, report):
    canvas = canvas_evidence(page)
    require(canvas["sampled_colors"] > 50 and canvas["opaque_samples"] > 100, f"{name}: canvas blank")
    require(canvas["bounds"]["width"] > 100 and canvas["bounds"]["height"] > 100, f"{name}: canvas improperly framed")
    layout = page.evaluate("""() => ({width: innerWidth, height: innerHeight,
        documentWidth: document.documentElement.scrollWidth,
        brokenImages: [...document.images].filter(image => !image.complete || !image.naturalWidth).map(image => image.src)})""")
    require(layout["documentWidth"] <= layout["width"]+1, f"{name}: horizontal page overflow")
    require(not layout["brokenImages"], f"{name}: broken images")
    path = output/f"v2-accepted-{name}.png"
    page.screenshot(path=str(path), full_page=True)
    report["screenshots"][name] = {"path": str(path), "canvas": canvas, "layout": layout}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", default="http://127.0.0.1:5180")
    parser.add_argument("--api", default="http://127.0.0.1:8772")
    parser.add_argument("--wait-running", type=float, default=120)
    parser.add_argument("--measure-motion", action="store_true", help="Explicitly start a paused episode, then pause after motion capture")
    parser.add_argument("--output-dir", type=Path, default=Path("outputs"))
    args = parser.parse_args()
    output = args.output_dir
    output.mkdir(parents=True, exist_ok=True)
    report = {"status": "failed", "started_at": datetime.now(timezone.utc).isoformat(), "ui": args.ui,
        "screenshots": {}, "errors": [], "console_errors": [], "page_errors": [], "blocked_writes": [], "operator_actions": [],
        "motion": {"verified": False}, "tracking": {"verified": False}, "annotation": {"submitted": False}}
    started = time.monotonic()
    with httpx.Client(base_url=args.api, timeout=15) as client, sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 900}, device_scale_factor=1)

        def readonly(route):
            if route.request.method not in ("GET", "HEAD", "OPTIONS"):
                report["blocked_writes"].append({"method": route.request.method, "url": route.request.url})
                route.abort()
            else:
                route.continue_()

        context.route("**/*", readonly)
        page = context.new_page()
        page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
        page.on("console", lambda message: report["console_errors"].append(message.text) if message.type == "error" else None)
        motion_episode = None

        def motion_command(operation):
            action = {"path": f"/api/simulation/{operation}", "episode_id": motion_episode,
                "test_operator_action": True, "reason": "explicit --measure-motion browser acceptance",
                "wall_time": datetime.now(timezone.utc).isoformat()}
            report["operator_actions"].append(action)
            action["response"] = client.post(action["path"], json={"episode_id": motion_episode}).raise_for_status().json()

        try:
            page.goto(args.ui, wait_until="networkidle")
            page.locator("canvas[aria-label='Operational map']").wait_for(state="visible")
            page.wait_for_timeout(800)
            report["discovered_controls"] = page.locator("button, [role=tab]").evaluate_all(
                "nodes => nodes.map(node => ({role: node.getAttribute('role'), label: node.getAttribute('aria-label') || node.textContent.trim()}))")
            report["initial_state"] = state_summary(client.get("/api/state").raise_for_status().json())
            capture(page, output, "desktop", report)
            report["assets"] = page.evaluate("""async () => {
                const urls = performance.getEntriesByType('resource').filter(entry => entry.initiatorType === 'img').map(entry => entry.name);
                return await Promise.all(urls.map(url => new Promise(resolve => {
                    const image = new Image(); image.onload = () => resolve({url, width: image.naturalWidth, height: image.naturalHeight});
                    image.onerror = () => resolve({url, width: 0, height: 0}); image.src = url;
                })));
            }""")
            require(any("background.png" in asset["url"] for asset in report["assets"]), "Background image was not requested")
            require(any("uuv.png" in asset["url"] for asset in report["assets"]), "UUV image was not requested")
            require(all(asset["width"] > 0 and asset["height"] > 0 for asset in report["assets"]), "Map asset failed to decode")
            page.get_by_role("tab", name="数据", exact=True).click()
            page.locator(".contact-panel").wait_for(state="visible")
            capture(page, output, "data", report)
            if not page.locator(".bottom-drawer").is_visible():
                page.get_by_role("button", name="切换任务详情面板", exact=True).click()
            page.get_by_role("tab", name="任务指标", exact=True).click()
            require(page.locator(".mission-metrics").is_visible(), "Metrics panel unavailable")
            report["metrics_text"] = page.locator(".mission-metrics").inner_text()
            capture(page, output, "metrics", report)
            page.get_by_role("button", name="关闭任务详情", exact=True).click()

            if args.measure_motion:
                initial = client.get("/api/state").raise_for_status().json()
                require(initial["runtime_status"] == "paused", "Explicit motion probe requires a paused episode")
                client.get("/api/health").raise_for_status()
                motion_episode = initial["episode_id"]
                motion_command("start")

            deadline = time.monotonic()+args.wait_running
            while time.monotonic() < deadline:
                before = client.get("/api/state").raise_for_status().json()
                tracked = [contact for contact in before["contacts"] if contact["state"] == "tracking"]
                if tracked and before["bearing_lines"] and not report["tracking"]["verified"]:
                    page.wait_for_timeout(300)
                    capture(page, output, "tracking", report)
                    report["tracking"] = {"verified": True, "state": state_summary(before),
                        "visible_contacts": page.locator(".contact-panel").inner_text()}
                if before["runtime_status"] == "running" and not report["motion"]["verified"]:
                    motion_started = time.monotonic()
                    canvas = page.locator("canvas[aria-label='Operational map']")
                    first_hash = hashlib.sha256(canvas.screenshot()).hexdigest()
                    page.wait_for_timeout(2000)
                    after = client.get("/api/state").raise_for_status().json()
                    last_hash = hashlib.sha256(canvas.screenshot()).hexdigest()
                    positions_before = {boat["id"]: boat["position"] for boat in before["uavs"]}
                    positions_after = {boat["id"]: boat["position"] for boat in after["uavs"]}
                    moved = [key for key in positions_before if positions_after.get(key) != positions_before[key]]
                    report["motion"] = {"verified": before["episode_id"] == after["episode_id"] and
                        after["runtime_status"] == "running" and bool(moved) and first_hash != last_hash,
                        "before": state_summary(before), "after": state_summary(after), "moved_boats": moved,
                        "positions_before": positions_before, "positions_after": positions_after,
                        "canvas_sha256_before": first_hash, "canvas_sha256_after": last_hash,
                        "elapsed_wall_s": round(time.monotonic()-motion_started, 3), "minimum_wait_ms": 2000}
                if report["motion"]["verified"] and report["tracking"]["verified"]:
                    break
                page.wait_for_timeout(700)

            if motion_episode:
                motion_command("pause")
                motion_episode = None

            page.get_by_role("tab", name="对话", exact=True).click()
            body = page.locator(".agent-message.assistant .markdown").filter(has_text=re.compile(r"\S{3}")).last
            body.wait_for(state="visible")
            body.scroll_into_view_if_needed()
            selected = body.evaluate("""element => {
                const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
                let text; while ((text = walker.nextNode())) {
                    if (text.textContent.trim().length < 3) continue;
                    const range = document.createRange(); range.setStart(text, 0); range.setEnd(text, Math.min(text.length, 100));
                    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    element.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    return {quote: selection.toString(), message_id: element.closest('[data-message-id]').dataset.messageId,
                        markdown_elements: element.querySelectorAll('strong,em,code,ul,ol,table').length};
                } return null;
            }""")
            require(selected is not None, "No rendered assistant text available")
            page.get_by_role("button", name="批注所选内容", exact=True).click()
            require(page.locator(".annotation-draft").is_visible(), "Rendered selection did not open annotation draft")
            require(page.locator(".annotation-draft blockquote").inner_text() == selected["quote"], "Annotation quote differs from selection")
            report["annotation"].update(selected, draft_opened=True)
            capture(page, output, "annotation", report)
            page.get_by_role("button", name="移除批注引用", exact=True).click()

            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(500)
            if not page.get_by_role("tab", name="对话", exact=True).is_visible():
                page.get_by_role("button", name="切换编队状态面板", exact=True).click()
            capture(page, output, "mobile-chat", report)
            page.get_by_role("tab", name="数据", exact=True).click()
            capture(page, output, "mobile-data", report)
            page.get_by_role("button", name="关闭编队状态", exact=True).click()
            page.wait_for_timeout(300)
            capture(page, output, "mobile", report)
            require(report["motion"]["verified"], "No genuine running boat/canvas motion observed within bounded wait")
            require(report["tracking"]["verified"], "No real tracking contact with bearing lines observed")
            require(not report["blocked_writes"], "UI attempted a write during read-only verification")
            require(not report["page_errors"] and not report["console_errors"], "Browser reported errors")
            report["status"] = "passed"
        except Exception as error:
            report["errors"].append({"type": type(error).__name__, "message": str(error)})
            page.screenshot(path=str(output/"v2-browser-failure.png"), full_page=True)
        finally:
            if motion_episode:
                try:
                    motion_command("pause")
                except Exception as error:
                    report["errors"].append({"type": type(error).__name__, "phase": "operator_cleanup", "message": str(error)})
                    report["status"] = "failed"
            browser.close()
    report["elapsed_wall_s"] = round(time.monotonic()-started, 3)
    (output/"v2-browser-acceptance.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"status": report["status"], "motion": report["motion"]["verified"], "tracking": report["tracking"]["verified"],
        "screenshots": list(report["screenshots"]), "errors": report["errors"]}, ensure_ascii=False))
    return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
