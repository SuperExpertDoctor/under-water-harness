# 验收记录

> 本文仅记录第一版实际验收，不覆盖 [第二版修改方案](../docs/superpowers/specs/2026-09-27-uuv-mission-control-v2-design.md) 的纯方位协同、动态分区、能源轮换和新会话界面。第二版尚未实施或验收。

日期：2026-09-27。范围是二维多UUV游戏演示与现有UI接入，不是实体UUV控制认证。未创建git commit。

## 已验证的闭环

1. Python真实计算与控制：Dubins六族、前向姿态搜索、条带闭环、槽位分配、距离带跟踪、固定步长运动。
2. Request/Assisted/Full代码权限门：候选不执行、待审批不阻塞时钟、审批后激活、旧episode拒绝、幂等回执、取消后拒绝迟到工具。
3. PI SDK实际加载Extension，仅注册9个任务工具；受信Skill描述流程，没有shell或任意文件工具。
4. LongCat-2.0真实服务调用：读状态；单艇规划/评估/提交待审批；浏览器批准后实体位置改变；8艇3+3+2分区规划、评估和提交全部成功。
5. 界面完整工作流：任务计算/组装/修改、审批/拒绝、模式切换、聊天/Skill作业取消、场景船舶/AIS、重点区、停止/重置、只读回放及真实MP4下载。

模型验收只认可 `tool_completed` 后端回执，不把模型输出的工具调用文字算作执行。调试初期空工具白名单曾造成这一问题，已通过显式9工具白名单修复并加入SDK测试。

## 自动测试

最终完整Python测试为97项通过，包含独立审查后补充的回归。

| 检查 | 实际结果 |
| --- | --- |
| `PYTHONPATH=tools python -m pytest tools/tests -q` | 97通过，28.99秒 |
| PI配置及实际SDK会话测试 | 4通过 |
| `npx tsc -p agent/tsconfig.json --noEmit` | 通过 |
| UI任务状态测试 | 7通过 |
| UI诊断渲染回归 | infeasible对象、timed_out对象、旧数组格式均显示 |
| `npm run check:browser-smoke` | 通过 |
| `npm run check` | 未通过整个仓库的TypeScript检查，详见下节 |

关键回归覆盖：拥塞不会杀死仿真循环、worker租约过期后队列恢复、取消与提交竞争、并行计算与reset、WS跨episode恢复、审批不依赖已淘汰候选、无关编队审批互不失效、部分成员换队不产生未授权盘旋、停止不能通过pause再start绕过、归档计划仍可查询、事务失败同时回滚检查点/回执/内存、规划避开待命艇、未知算法与非有限输入拒绝。

## 仓库检查例外

`npm run check`中的格式、固定依赖、运行依赖、import、入口图、shrinkwrap及install-lock检查全部通过，格式器报告1482个文件且无修改。失败发生在以下现有测试文件引用的Fireworks模型标识与本机水合生成的模型目录不一致：

- `packages/ai/test/anthropic-empty-thinking-signature-compat.test.ts`
- `packages/ai/test/fireworks-models.test.ts`
- `packages/ai/test/openai-completions-prompt-cache.test.ts`

例如测试仍引用 `glm-5p2`，生成目录类型已包含 `glm-5p3`。本次没有为通过检查而改动无关上游测试或伪造模型条目。独立PI桥接类型检查和LongCat实际调用均通过。不能把本项目验收通过说成整个monorepo检查全绿。

## 场景测试

- 八艇真实搜索：3+3+2分区，129仿真秒首次确认默认目标。
- 辅助审批：最近一艘搜索艇转为跟踪候选，风险规则要求人工批准；批准后持续600仿真秒跟踪，另外7艘继续搜索。
- 丢失与重搜索：关闭测试传感器，接触丢失且跟踪保护性暂停；用最后估计区域计算重搜索，批准并人工恢复后重新确认目标。
- 四小时加速运行：8艘在场、其中1艘执行闭合搜索，目标为空以隔离长期运行机制；72000步、14400仿真秒、至少7次闭合重访，无模型连接，运行未暂停。
- 八艇无目标压力探测：在约71.14仿真分钟触发保守避碰保护暂停，未继续运动。这验证了保护路径，也说明本启发式并非保证任意八艇航线无限无干预运行的全局调度器。
- 真实进程故障：在八艇已有授权计划的条件下终止本次启动的PI worker；监护程序重新启动worker，八艘艇均继续运动，任务episode保持不变。测试结束后由验收脚本人工暂停，仿真时间53.4秒。后台API随后重新启动并成功恢复三份活动计划。
- 场景船舶：新增船舶与仿真目标使用相同ID，实际运动；AIS开启产生AIS观测，关闭后需要附近传感器；删除同时清理运动实体和接触。覆盖7项场景/时间线回归测试。
- 30分钟墙钟soak通过：1800.001秒墙钟、17999.8秒仿真（约5小时）、9次搜索闭环；全程没有模型连接，中点检查点关闭/重开后状态保持。29次分钟采样的进程峰值RSS均为117976 KiB（约115.2 MiB），待决策队列保持1项；最后磁盘采样约117.5 MiB，主要为有保留上限的回放帧。

复现核心场景：`tools/tests/test_scenarios.py`；墙钟测试现位于 `tools/acceptance/soak.py`。Soak采用无目标单艇闭环，其他7艘待命；它与八艇发现/跟踪测试是两类独立证据，不混称为八艇连续跟踪四小时。

## 浏览器证据

Playwright最终检查1440x900、390x844、844x390；无页面脚本错误、无水平溢出，Canvas实际非空且航行时像素与艇位均变化。最终真实MP4下载为104741字节，ffprobe检查为H.264、1100x522、0.897196秒。这是短回放导出测试，不是假称导出了整场长时任务。文件和可复现脚本详见 [UI集成报告](../ui/INTEGRATION.md)。

主要忽略目录证据：

- `tools/artifacts/longcat-read.json`：真实状态读取回执。
- `tools/artifacts/longcat-mission.json`：真实单艇计划、校验、待审批提交。
- `tools/artifacts/longcat-fleet.json`：真实8艇分配、三份计划及执行回执。
- `tools/artifacts/longcat-chat.png`、`longcat-approval.png`、`longcat-executed.png`、`longcat-fleet.png`：真实模型与UI衔接截图。
- `tools/artifacts/browser/`：独立浏览器全流程截图、结果和MP4。
- `tools/artifacts/browser-final/`：最终源码版本的隔离浏览器复验。
- `tools/artifacts/worker-restart.json`：真实worker重启与八艇运动记录。
- `tools/artifacts/soak.json`：已完成的墙钟测试及逐分钟采样。

## 当前交接状态

前端 `http://127.0.0.1:5180`，后端 `http://127.0.0.1:8772`。监护进程运行，PI为LongCat-2.0、idle、无错误；仿真由验收脚本人工暂停在53.4秒，8艘艇按3+3+2保留三份有效搜索计划。点击顶部启动按钮即可继续，避免在交接期间无人值守地产生模型调用费用。当前端口和PID以 `./run.sh --status` 为准。

## 运行边界

长期服务依靠独立时钟、串行决策队列、监护进程和持久化实现，不是无限延长一个模型请求。Full依然受人数、几何、授权域和碰撞保护约束；保护暂停需要人工恢复。算法是有明确预算与失败语义的演示启发式，不宣称完备、最优或真实水下物理保证。

本机凭证只存忽略的权限受限文件，没有进入UI或版本文件；仍建议轮换曾在聊天中提供过的API密钥。服务仅监听回环地址，非公网多用户安全产品。
