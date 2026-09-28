"""Explicit browser/operator acceptance, including one real LongCat annotation."""
import hashlib
import json
from pathlib import Path
import time

import httpx
from playwright.sync_api import sync_playwright

from v2_browser_acceptance import canvas_evidence, require
from v2_live_acceptance import assignment


def main():
    services = json.loads(Path("tools/.runtime/services.json").read_text())
    report = {"status": "failed", "checks": {}, "errors": [], "screenshots": [], "writes": []}
    with httpx.Client(base_url=services["backend_url"], timeout=20) as client, sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        frames = []
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        page.on("console", lambda msg: report["errors"].append(msg.text) if msg.type == "error" else None)
        page.on("request", lambda req: report["writes"].append({"url": req.url, "body": req.post_data})
                if req.method == "POST" else None)

        def receive(payload):
            try:
                value = json.loads(payload)
                if "target_info_matrix" in value:
                    frames.append(value)
                    del frames[:-4]
            except (ValueError, TypeError):
                pass

        page.on("websocket", lambda ws: ws.on("framereceived", receive))

        def state():
            return client.get("/api/state").raise_for_status().json()

        def capture(name):
            evidence = canvas_evidence(page)
            require(evidence["sampled_colors"] > 100, "Blank map")
            require(page.evaluate("document.documentElement.scrollWidth <= innerWidth+1"), "Horizontal overflow")
            path = f"outputs/v4-{name}.png"
            page.screenshot(path=path, full_page=True)
            report["screenshots"].append({"path": path, "canvas": evidence})

        try:
            initial = state()
            require(initial["runtime_status"] == "paused", "Run only after live workflow has paused")
            page.goto(services["ui_url"], wait_until="networkidle")
            page.locator(".map-summary").wait_for()
            page.wait_for_timeout(1200)
            require(frames, "No live information websocket frame")
            current = state()
            owned = [tuple(cell) for region in current["search_regions"] for cell in region["cells"]]
            require(len(owned) == len(set(owned)) == current["searchable_cells"], "Search regions do not cover the complete searchable area")
            owners = {region["assigned_uav_id"] for region in current["search_regions"]}
            require(len(owners) == len(current["search_regions"]) == current["mission_metrics"]["search_boats"], "Search region/boat mismatch")
            require(not owners.intersection(boat["id"] for boat in current["uavs"] if boat["operation_mode"] == "track"), "Tracking boat owns search region")
            report["checks"]["full_area_unique_search_ownership"] = True
            for key in ("info_matrix", "target_info_matrix", "information_model"):
                require(frames[-1][key] == current[key], f"HTTP/WS mismatch: {key}")
            report["checks"]["http_websocket_fields_match"] = True
            canvas = page.get_by_label("Operational map", exact=True)
            canvas.focus()
            for _ in range(10):
                canvas.press("ArrowRight")
            for _ in range(12):
                canvas.press("ArrowDown")
            reading = page.get_by_label("栅格信息", exact=True).inner_text()
            require("10 / 12" in reading, "Keyboard cell selection failed")
            require(f'{current["info_matrix"][10][12]*100:.1f}%' in reading, "Cell scan value differs from backend")
            require(f'{current["target_info_matrix"][10][12]*100:.1f}%' in reading, "Cell target value differs from backend")
            report["checks"]["keyboard_cell_matches_backend"] = True
            capture("desktop")
            canvas.press("Escape")
            require("--" in page.get_by_label("栅格信息", exact=True).inner_text(), "Escape did not clear cell")

            page.get_by_role("tab", name="数据", exact=True).click()
            require(page.locator(".uuv-row").count() == 8, "Data panel must show eight boats")
            capture("data")
            page.get_by_role("tab", name="任务指标", exact=True).click()
            capture("metrics")
            page.get_by_role("tab", name="对话", exact=True).click()
            page.get_by_role("tab", name="时间线", exact=True).click()

            original = page.locator(".agent-message.assistant .markdown").last
            original.scroll_into_view_if_needed()
            selection = original.evaluate("""element => {
                const walker = document.createTreeWalker(element, NodeFilter.SHOW_TEXT);
                let node; while ((node = walker.nextNode())) {
                    if (node.textContent.trim().length < 3) continue;
                    const range = document.createRange(); range.setStart(node, 0); range.setEnd(node, Math.min(node.length, 80));
                    const selection = window.getSelection(); selection.removeAllRanges(); selection.addRange(range);
                    element.dispatchEvent(new MouseEvent('mouseup', {bubbles: true}));
                    return {quote: selection.toString(), message_id: element.closest('[data-message-id]').dataset.messageId};
                } return null;
            }""")
            require(selection, "Cannot select actual assistant text")
            page.get_by_role("button", name="批注所选内容", exact=True).click()
            require(page.locator(".annotation-draft blockquote").inner_text() == selection["quote"], "Quote was changed")
            capture("annotation")
            before = assignment(state())
            page.get_by_label("消息投递方式", exact=True).select_option("followUp")
            page.get_by_label("任务指令或批注", exact=True).fill(
                "请只读查询当前任务状态，用中文简要解释这段引用对应的实际任务情况。保留所有已批准计划、艇的分配和暂停状态，"
                "不要规划、评估、提交、批准或启动仿真。这是浏览器批注验收。")
            with page.expect_response(lambda response: response.url.endswith("/api/pi-agent/messages")
                                      and response.request.method == "POST", timeout=30000) as response:
                page.get_by_role("button", name="发送消息", exact=True).click()
            require(response.value.ok, "Annotation POST rejected")
            run_id = response.value.json()["run_id"]
            report["annotation"] = {**selection, "run_id": run_id}
            deadline = time.monotonic()+300
            while time.monotonic() < deadline:
                jobs = client.get("/api/pi-agent/task-assignment").raise_for_status().json()["assignments"]
                job = next(item for item in jobs if item["run_id"] == run_id)
                if job["status"] in ("completed", "failed", "cancelled"):
                    require(job["status"] == "completed", f'LongCat annotation ended {job["status"]}')
                    break
                page.wait_for_timeout(1000)
            else:
                raise AssertionError("Real model annotation timed out")
            page.wait_for_timeout(1200)
            messages = client.get("/api/pi-agent/messages").raise_for_status().json()["messages"]
            replies = [item for item in messages if item.get("run_id") == run_id and item["role"] == "assistant"
                       and "longcat" in item.get("model", "").lower() and item.get("text")]
            require(replies, "No real LongCat reply")
            require(page.locator(f'[data-message-id="{replies[-1]["id"]}"]').count() == 1, "Reply not rendered in browser")
            require(assignment(state()) == before and state()["runtime_status"] == "paused", "Read-only annotation changed execution")
            report["checks"]["real_annotation_round_trip"] = True
            report["annotation"]["reply_id"] = replies[-1]["id"]
            capture("conversation")

            page.locator("summary").filter(has_text="任务与算法").click()
            with page.expect_response(lambda response: response.url.endswith("/api/algorithm/task-plan")
                                      and response.request.method == "POST", timeout=90000) as response:
                page.get_by_role("button", name="计算候选", exact=True).click()
            require(response.value.ok, "Automatic full-area planner UI request rejected")
            candidate = response.value.json()
            require(candidate["status"] == "succeeded", f'Global candidate failed: {candidate.get("diagnostics")}')
            require(set(candidate["members"]) == owners, "Automatic planning selected unavailable boats")
            require(assignment(state()) == before, "Preview calculation changed live assignments")
            report["checks"]["automatic_full_area_planning_ui"] = True
            page.get_by_role("button", name="清除预览", exact=True).click()
            page.locator("summary").filter(has_text="任务与算法").click()

            for width, height, name in [(390, 844, "mobile"), (375, 812, "small-mobile"), (844, 390, "landscape")]:
                page.set_viewport_size({"width": width, "height": height})
                page.emulate_media(reduced_motion="reduce")
                page.wait_for_timeout(300)
                capture(name)
            page.set_viewport_size({"width": 1440, "height": 960})
            page.emulate_media(reduced_motion="no-preference")
            page.get_by_role("button", name="启动仿真", exact=True).click()
            page.wait_for_timeout(1200)
            first = state()
            image_before = hashlib.sha256(canvas.screenshot()).hexdigest()
            page.wait_for_timeout(2200)
            second = state()
            require(first["uavs"][0]["position"] != second["uavs"][0]["position"], "Boat did not move")
            require(image_before != hashlib.sha256(canvas.screenshot()).hexdigest(), "Canvas did not update")
            page.get_by_role("button", name="暂停仿真", exact=True).click()
            page.wait_for_timeout(500)
            require(state()["runtime_status"] == "paused", "Pause control failed")
            report["checks"]["ui_start_motion_pause"] = True
            require(not report["errors"], "Browser errors")
            report["status"] = "passed"
        except Exception as error:
            report["errors"].append(str(error))
            page.screenshot(path="outputs/v4-interaction-failure.png", full_page=True)
        finally:
            current = state()
            if current["episode_id"] == initial["episode_id"] and current["runtime_status"] == "running":
                client.get("/api/health").raise_for_status()
                client.post("/api/simulation/pause", json={"episode_id": initial["episode_id"]}).raise_for_status()
            browser.close()
    Path("outputs/v4-interaction.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"status": report["status"], "checks": report["checks"], "errors": report["errors"]}, ensure_ascii=False))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
