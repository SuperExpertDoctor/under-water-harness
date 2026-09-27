"""Candidate diagnostics regression; intercepts calculation requests without mutating the API."""

import argparse
import json
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True)
options = parser.parse_args()

with sync_playwright() as playwright:
    browser = playwright.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900})
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    candidate = {}
    page.route("**/api/algorithm/task-plan", lambda route: route.fulfill(json=candidate))
    page.route("**/api/plans/diagnostic-test", lambda route: route.fulfill(json=candidate))
    page.goto(options.url)
    page.wait_for_load_state("networkidle")
    episode = page.request.get(options.url.rstrip("/") + "/api/state").json()["episode_id"]
    page.get_by_role("tab", name="任务", exact=True).click()
    for status, diagnostics in [
        ("infeasible", {"reason": "search_box_too_small", "minimum_width_m": 600}),
        ("timed_out", {"reason": "budget_exhausted", "expanded": 3000}),
        ("infeasible", ["blocked_route"]),
    ]:
        candidate = {"episode_id": episode, "result_id": "diagnostic-test", "status": status, "diagnostics": diagnostics}
        compute = page.get_by_role("button", name="计算候选", exact=True)
        expect(compute).to_be_enabled()
        compute.click()
        expect(page.locator(".candidate-detail pre")).to_have_text(json.dumps(diagnostics, indent=2))
        expect(page.get_by_role("button", name="校验", exact=True)).to_be_disabled()
    assert not errors, errors
    print("PASS: object infeasible/timed_out and array diagnostics are visible; no backend mutation")
    browser.close()
