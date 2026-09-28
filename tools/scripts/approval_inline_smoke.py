"""Read-only browser fixture for the inline UUV approval and decision table."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    output = Path(__file__).resolve().parents[2] / "outputs"
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for label, width, height in (("desktop", 1440, 900), ("mobile", 390, 844), ("small", 375, 667), ("landscape", 844, 390)):
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            status = {"value": "pending_approval", "scope": None, "submissions": 0}
            plan_id = "plan-visual-fixture"
            run_id = "run-visual-fixture"

            def state_route(route):
                frame = route.fetch().json()
                frame["plans"] = [{"plan_id": plan_id, "kind": "search", "members": ["UUV-1", "UUV-3"],
                    "status": status["value"], "approval_scope": status["scope"], "reason": "human_required",
                    "risk": .42, "created_at_s": 72, "expires_at_s": 620, "decision_reason": "北侧搜索区覆盖不足，安排两艇补齐观测空白。",
                    "execution_domain": [0, 600, 2200, 3600], "domain_policy": "route_envelope_including_transit"}]
                frame["events"] = [{"id": 9001, "episode_id": frame["episode_id"], "time": 1.2, "type": "approval_requested", "data": {"plan_id": plan_id, "members": ["UUV-1", "UUV-3"]}},
                    {"id": 9002, "episode_id": frame["episode_id"], "time": 1.2, "type": "tool_completed", "data": {"plan_id": plan_id, "run_id": run_id, "tool": "submit_mission_plan"}}]
                frame["event_cursor"] = 9002
                route.fulfill(json=frame)

            def message_route(route):
                route.fulfill(json={"messages": [{"id": "assistant-visual", "role": "assistant", "run_id": run_id,
                    "text": "已形成搜索计划，正等待授权后执行调度。", "status": "streaming"}]})

            def decision_route(route):
                decision = route.request.post_data_json["decision"]
                assert decision in ("approve_once", "approve_session", "reject")
                status.update(value="active" if decision != "reject" else "rejected", scope=decision, submissions=status["submissions"] + 1)
                route.fulfill(json={"plan_id": plan_id, "status": status["value"], "approval_scope": decision})

            page.route_web_socket("**/ws/**", lambda _ws: None)
            page.route("**/api/state", state_route)
            page.route("**/api/pi-agent/messages", message_route)
            page.route(f"**/api/approvals/{plan_id}/decision", decision_route)
            page.goto(os.environ.get("UUV_UI_SMOKE_URL", "http://127.0.0.1:5209/"))
            if label != "desktop":
                page.get_by_role("button", name="切换编队状态面板").click()
            card = page.locator(".approval-request.pending")
            card.wait_for(state="visible", timeout=15000)
            page.locator(".decision-table").wait_for(state="attached")
            history = page.locator(".agent-conversation").bounding_box()
            bounds = card.bounding_box()
            if label in ("desktop", "mobile"):
                assert bounds["y"] >= history["y"] - 1, (label, bounds, history)
                assert bounds["y"] + bounds["height"] <= history["y"] + history["height"] + 1, (label, bounds, history)
            assert page.locator(".approval-blocker").count() == 0
            assert page.get_by_text("北侧搜索区覆盖不足，安排两艇补齐观测空白。").count() >= 2
            assert page.locator(".approval-options input[type=radio]").count() == 3
            assert page.get_by_role("button", name="提交").is_visible()
            assert page.evaluate("document.body.scrollWidth <= innerWidth + 1")
            page.screenshot(path=str(output / f"approval-inline-{label}.png"), full_page=True)
            page.get_by_role("button", name="提交").scroll_into_view_if_needed()
            if label == "desktop":
                page.locator(".approval-options input[value=approve_session]").check()
                assert status["submissions"] == 0
                page.get_by_role("button", name="提交").click()
                page.locator(".approval-request.resolved").wait_for(state="visible")
                assert status["scope"] == "approve_session"
                assert page.locator(".approval-request.pending").count() == 0
                assert page.locator(".decision-table tbody tr").first.get_by_text("执行中").is_visible()
                page.screenshot(path=str(output / "approval-inline-resolved.png"), full_page=True)
            elif label == "mobile":
                page.get_by_role("button", name="关闭编队状态").click()
                assert page.locator(".decision-table").is_visible()
                assert page.locator(".decision-table tbody tr").first.get_by_text("UUV-1、UUV-3").is_visible()
                page.screenshot(path=str(output / "approval-inline-mobile-table.png"), full_page=True)
            assert not errors, errors
            print(label, "inline approval and decision table visible; page errors:", errors)
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
