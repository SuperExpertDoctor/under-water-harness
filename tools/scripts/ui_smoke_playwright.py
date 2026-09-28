"""Read-only visual smoke check against a running local UI."""

import os
from pathlib import Path

from playwright.sync_api import sync_playwright


def main():
    output = Path(__file__).resolve().parents[2] / "outputs"
    output.mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        for label, width, height in (("desktop", 1440, 900), ("mobile", 390, 844)):
            page = browser.new_page(viewport={"width": width, "height": height}, device_scale_factor=1)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(os.environ.get("UUV_UI_SMOKE_URL", "http://127.0.0.1:5187/"))
            page.wait_for_load_state("domcontentloaded")
            page.locator("canvas").first.wait_for(state="visible")
            page.locator(".conversation-panel").wait_for(state="attached")
            page.screenshot(path=str(output / f"mission-sonar-{label}.png"), full_page=True)
            dimensions = page.evaluate("""() => ({
                bodyWidth: document.body.scrollWidth,
                viewportWidth: innerWidth,
                canvas: [...document.querySelectorAll('canvas')].map(c => ({ width: c.width, height: c.height })),
                chat: Boolean(document.querySelector('.conversation-panel')),
            })""")
            print(label, dimensions, errors)
            assert not errors, errors
            assert all(canvas["width"] > 0 and canvas["height"] > 0 for canvas in dimensions["canvas"])
            assert dimensions["bodyWidth"] <= dimensions["viewportWidth"] + 1
            if label == "mobile":
                page.get_by_role("button", name="切换编队状态面板").click()
                page.locator(".conversation-panel").wait_for(state="visible")
                approvals = page.get_by_role("region", name="待批准计划")
                if approvals.count():
                    assert approvals.is_visible()
                page.screenshot(path=str(output / "mission-sonar-mobile-chat.png"), full_page=True)
                assert page.evaluate("document.body.scrollWidth <= innerWidth + 1")
            page.close()
        browser.close()


if __name__ == "__main__":
    main()
