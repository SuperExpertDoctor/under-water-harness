"""Destructive browser acceptance: use an isolated API database with no PI worker."""

import argparse
import hashlib
import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--url", required=True, help="Isolated UI URL; this test resets its mission")
parser.add_argument("--output-dir", required=True, type=Path)
options = parser.parse_args()
BASE = options.url.rstrip("/")
OUT = options.output_dir
OUT.mkdir(parents=True, exist_ok=True)
results = {}

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page(viewport={"width": 1440, "height": 900}, accept_downloads=True)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("dialog", lambda dialog: dialog.accept())
    page.goto(BASE)
    page.wait_for_load_state("networkidle")
    expect(page.get_by_role("button", name="启动仿真")).to_be_enabled()

    def action(name, endpoint):
        button = page.get_by_role("button", name=name, exact=True)
        expect(button).to_be_enabled(timeout=30000)
        with page.expect_response(lambda response: endpoint in response.url and response.request.method != "GET", timeout=120000) as pending:
            button.click()
        response = pending.value
        data = response.json()
        assert response.ok, (endpoint, response.status, data)
        return data

    def state():
        return page.request.get(BASE + "/api/state").json()

    action("重置回合", "/api/simulation/reset")
    action("请求", "/api/permissions/mode")
    page.get_by_role("tab", name="任务", exact=True).click()
    computed = action("计算候选", "/api/algorithm/task-plan")
    assert computed["status"] == "succeeded", computed
    results["candidate"] = {"status": computed["status"], "domain": computed.get("execution_domain"), "policy": computed.get("domain_policy")}
    expect(page.locator(".candidate-detail")).to_be_visible(timeout=30000)
    checked = action("校验", "/api/algorithm/decision")
    assert checked["valid"] and checked["requires_approval"], checked
    task = action("装配任务", "/api/task-assembly/assemble")
    expect(page.locator(".task-row")).to_have_count(1)
    page.locator(".task-row input").fill("已修改装配任务")
    action("保存", "/api/task-assembly/tasks/")
    results["task_assemble_rename"] = True
    submitted = action("提交权限检查", "/api/algorithm/commands")
    assert submitted["status"] == "pending_approval", submitted
    page.get_by_role("tab", name="审批", exact=False).click()
    expect(page.get_by_text("授权范围（含转场）", exact=True)).to_be_visible()
    page.screenshot(path=str(OUT / "approval-desktop.png"), full_page=True)
    before_pending = state()
    action("启动仿真", "/api/simulation/start")
    page.wait_for_timeout(1700)
    action("暂停仿真", "/api/simulation/pause")
    pending_state = state()
    assert pending_state["sim_time_min"] > before_pending["sim_time_min"]
    assert pending_state["pending_approvals"], pending_state["plans"]
    results["simulation_advances_pending_approval"] = True
    approved = action("批准", "/api/approvals/")
    assert approved["status"] == "active", approved
    before = state()
    canvas_before = hashlib.sha256(page.locator("canvas").screenshot()).hexdigest()
    action("启动仿真", "/api/simulation/start")
    page.wait_for_timeout(3500)
    action("暂停仿真", "/api/simulation/pause")
    after = state()
    canvas_after = hashlib.sha256(page.locator("canvas").screenshot()).hexdigest()
    moved = [u["id"] for u, old in zip(after["uavs"], before["uavs"]) if u["position"] != old["position"]]
    assert set(computed["members"]).issubset(set(moved)), moved
    assert canvas_before != canvas_after
    results["moving_uuvs"] = moved
    results["canvas_motion"] = True
    page.screenshot(path=str(OUT / "moving-desktop.png"), full_page=True)

    page.get_by_role("tab", name="态势", exact=True).click()
    action_button = page.get_by_role("button", name="II 类船舶", exact=True)
    action_button.click()
    with page.expect_response(lambda response: response.url.endswith("/api/vessels") and response.request.method == "POST") as vessel_response:
        page.locator("canvas").click(position={"x": 650, "y": 280})
    assert vessel_response.value.ok
    expect(page.locator(".scenario-vessel-row")).to_have_count(1)
    page.locator(".scenario-vessel-row").click()
    action("关闭 AIS", "/ais")
    expect(page.get_by_role("button", name="开启 AIS", exact=True)).to_be_enabled()
    action("开启 AIS", "/ais")
    action("删除选中船舶", "/api/vessels/")
    expect(page.locator(".scenario-vessel-row")).to_have_count(0)
    results["vessel_create_ais_delete"] = True

    page.get_by_role("button", name="框选重点区", exact=True).click()
    box = page.locator("canvas").bounding_box()
    page.mouse.move(box["x"] + 620, box["y"] + 240)
    page.mouse.down()
    page.mouse.move(box["x"] + 730, box["y"] + 350, steps=8)
    page.mouse.up()
    intent = page.get_by_role("region", name="人工重点区")
    intent.get_by_label("名称", exact=True).fill("浏览器验收重点区")
    action("提交重点区", "/api/intents")
    expect(intent.get_by_text("浏览器验收重点区", exact=True)).to_be_visible()
    page.get_by_role("button", name="编辑 浏览器验收重点区", exact=True).click()
    intent.get_by_label("名称", exact=True).fill("已修改重点区")
    action("提交修改", "/api/intents/")
    expect(intent.get_by_text("已修改重点区", exact=True)).to_be_visible()
    action("取消 已修改重点区", "/api/intents/")
    expect(intent.get_by_text("已取消", exact=True)).to_be_visible()
    results["intent_create_edit_cancel"] = True

    stopped = action("停止任务", "/api/simulation/stop")
    assert stopped["status"] == "stopped"
    expect(page.get_by_role("button", name="启动仿真")).to_be_disabled()
    episode = state()["episode_id"]
    action("重置回合", "/api/simulation/reset")
    expect(page.get_by_role("button", name="启动仿真")).to_be_enabled(timeout=10000)
    assert state()["episode_id"] != episode
    assert len(state()["uavs"]) == 8
    results["stop_reset"] = True
    action("请求", "/api/permissions/mode")
    page.get_by_role("tab", name="任务", exact=True).click()
    action("计算候选", "/api/algorithm/task-plan")
    action("校验", "/api/algorithm/decision")
    action("提交权限检查", "/api/algorithm/commands")
    page.get_by_role("tab", name="审批", exact=False).click()
    rejected = action("拒绝", "/api/approvals/")
    assert rejected["status"] == "rejected"
    action("全自主", "/api/permissions/mode")
    expect(page.get_by_role("button", name="全自主", exact=True)).to_have_attribute("aria-pressed", "true")
    action("辅助", "/api/permissions/mode")
    expect(page.get_by_role("button", name="辅助", exact=True)).to_have_attribute("aria-pressed", "true")
    results["reject_and_mode_switches"] = True
    page.get_by_role("tab", name="任务", exact=True).click()
    skill_job = action("执行技能", "/execute")
    assert skill_job["status"] == "queued"
    action("取消技能运行", "/cancel")
    page.get_by_role("tab", name="对话", exact=True).click()
    page.get_by_label("任务指令", exact=True).fill("浏览器验收请求，不连接模型")
    chat_job = action("发送", "/api/pi-agent/messages")
    assert chat_job["status"] == "queued"
    action("取消决策", "/cancel")
    results["skill_and_chat_queue_cancel_without_worker"] = True
    page.get_by_role("button", name="回放", exact=True).click()
    expect(page.get_by_role("button", name="启动仿真")).to_be_disabled()
    page.get_by_role("combobox", name="选择回放文件").select_option(episode)
    expect(page.get_by_role("button", name="播放", exact=True)).to_be_enabled(timeout=10000)
    page.get_by_role("button", name="下一帧", exact=True).click()
    results["replay_readonly_seek"] = True
    export = page.get_by_role("button", name="Export MP4", exact=True)
    if export.is_enabled():
        with page.expect_download(timeout=180000) as download:
            export.click()
        file = OUT / "browser-export.mp4"
        download.value.save_as(str(file))
        data = file.read_bytes()
        assert len(data) > 1000 and b"ftyp" in data[:32]
        results["mp4"] = {"bytes": len(data), "file": str(file)}
    else:
        results["mp4"] = {"available": False, "reason": export.get_attribute("title")}
    page.screenshot(path=str(OUT / "replay-desktop.png"), full_page=True)
    for viewport in [{"width": 390, "height": 844}, {"width": 844, "height": 390}]:
        page.set_viewport_size(viewport)
        page.wait_for_timeout(500)
        close = page.get_by_role("button", name="关闭编队状态", exact=True)
        if close.is_visible():
            close.click()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert page.locator("canvas").bounding_box()["height"] >= 100
        page.screenshot(path=str(OUT / f"replay-{viewport['width']}.png"), full_page=True)
    results["page_errors"] = errors
    assert not errors, errors
    (OUT / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(results, ensure_ascii=False, indent=2), flush=True)
    browser.close()
