"""Read-only browser verification of the live partially observable console."""
import argparse
import hashlib
import json
from pathlib import Path

from playwright.sync_api import sync_playwright

from v2_browser_acceptance import canvas_evidence, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ui", required=True)
    parser.add_argument("--prefix", default="v3")
    parser.add_argument("--motion", action="store_true")
    args = parser.parse_args()
    output = Path("outputs")
    output.mkdir(exist_ok=True)
    report = {"status": "failed", "screenshots": {}, "errors": [], "assets": [], "motion": None}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 960})
        page.on("pageerror", lambda error: report["errors"].append(str(error)))
        page.on("console", lambda msg: report["errors"].append(msg.text) if msg.type == "error" else None)

        def capture(name):
            evidence = canvas_evidence(page)
            require(evidence["sampled_colors"] > 100, "Blank map")
            layout = page.evaluate("""() => ({width: innerWidth, documentWidth: document.documentElement.scrollWidth,
                map: document.querySelector('.map-stage').getBoundingClientRect().toJSON(),
                summary: document.querySelector('.map-summary').getBoundingClientRect().toJSON()})""")
            require(layout["documentWidth"] <= layout["width"] + 1, "Horizontal overflow")
            require(layout["map"]["y"] >= layout["summary"]["bottom"] - 1, "Map and header overlap")
            path = output / f"{args.prefix}-{name}.png"
            page.screenshot(path=str(path), full_page=True)
            report["screenshots"][name] = {"path": str(path), "canvas": evidence, "layout": layout}

        try:
            page.goto(args.ui, wait_until="networkidle")
            page.locator(".map-summary").wait_for()
            page.wait_for_timeout(800)
            require(page.get_by_role("button", name="切换场景真值图层").count() == 0, "Truth control is exposed")
            report["assets"] = page.evaluate("""async () => {
              const urls = performance.getEntriesByType('resource').filter(e => e.initiatorType === 'img').map(e => e.name);
              return Promise.all(urls.map(url => new Promise(resolve => {const img = new Image();
                img.onload = () => resolve({url, width: img.naturalWidth, height: img.naturalHeight});
                img.onerror = () => resolve({url, width: 0}); img.src = url;})));
            }""")
            for asset in ["background.png", "uuv.png", "submarine-transparent.png"]:
                require(any(asset in row["url"] and row["width"] > 0 for row in report["assets"]), f"Missing {asset}")
            capture("desktop")
            keys = page.locator(".owner-key")
            report["ownership"] = keys.all_text_contents()
            if keys.count():
                keys.first.click()
                require(keys.first.get_attribute("aria-pressed") == "true", "Owner selection did not take effect")
                keys.first.click()
            if args.motion:
                canvas = page.locator("canvas[aria-label='Operational map']")
                before = hashlib.sha256(canvas.screenshot()).hexdigest()
                page.wait_for_timeout(2500)
                after = hashlib.sha256(canvas.screenshot()).hexdigest()
                report["motion"] = before != after
                require(report["motion"], "Canvas did not move")
            page.get_by_role("tab", name="数据", exact=True).click()
            require(page.get_by_text("场景编辑 · 调试真值", exact=True).count() == 0, "Scene editor exposed")
            capture("data")
            page.get_by_role("tab", name="任务指标", exact=True).click()
            capture("metrics")
            page.get_by_role("tab", name="对话", exact=True).click()
            page.get_by_role("tab", name="时间线", exact=True).click()
            page.set_viewport_size({"width": 390, "height": 844})
            page.wait_for_timeout(400)
            capture("mobile")
            page.get_by_role("button", name="切换编队状态面板", exact=True).click()
            capture("mobile-chat")
            require(not report["errors"], "Browser errors")
            report["status"] = "passed"
        except Exception as error:
            report["errors"].append(str(error))
            page.screenshot(path=str(output / f"{args.prefix}-browser-failure.png"), full_page=True)
        finally:
            browser.close()
    (output / f"{args.prefix}-browser.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps({"status": report["status"], "errors": report["errors"], "screenshots": list(report["screenshots"])}, ensure_ascii=False))
    return int(report["status"] != "passed")


if __name__ == "__main__":
    raise SystemExit(main())
