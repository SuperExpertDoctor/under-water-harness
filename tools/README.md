# PI 多 UUV 任务演示

现有 `ui/` 已接入 Python 仿真和 PI SDK。地图、聊天、审批、任务操作和底部事件时间线共享同一份后端状态。共 8 艘己方 UUV，每个任务编队最多 3 艘；不模拟弱通信。

## 启动

建议 Python 3.13、Node 22.22 或兼容版本。MP4 导出另外需要系统 `ffmpeg`。

```sh
npm ci --ignore-scripts
npm run hydrate:model-data
npm --prefix ui ci --ignore-scripts
python -m venv tools/.venv
tools/.venv/bin/pip install -r tools/requirements.txt
```

通过环境变量提供 `LONGCAT_API_KEY`，或使用本机忽略文件 `tools/.runtime/credentials.env`。文件内容是 `LONGCAT_API_KEY=实际密钥`，目录权限应为 700、文件权限为 600。不要放进 `ui/`、Vite 环境变量、命令行参数或版本库。当前工作区已经配置凭证，无需重复填写。

```sh
tools/.venv/bin/python tools/scripts/run.py
tools/.venv/bin/python tools/scripts/run.py --status
tools/.venv/bin/python tools/scripts/run.py --stop
```

启动器默认选择后端 8765、前端 5173；端口占用时自动选择其他端口，真实地址记录在 `tools/.runtime/services.json`。`--no-model` 可运行真实算法与界面而不请求模型，界面会显示 PI 离线，不伪造模型回复。`--foreground` 用于终端前台管理。

当前环境可直接使用 `python tools/scripts/run.py --status` 查看已经启动的服务。

## 界面与操作

- 左侧：现有二维地图、8 艘艇、历史轨迹、实际规划路径、传感覆盖、估计接触、编队与候选预览。
- 右侧：态势、对话、审批、任务四个页签。对话来自真实 LongCat；任务页也可直接计算、校验、提交 Python 候选。
- 顶部：仿真开始、暂停、停止、重置，以及请求批准、辅助批准、完全自主三种模式。停止后需重置开启新任务。
- 下方：时间线、区域、模型日志、参数和 AIS。这里是事件及执行记录，不是模型隐藏推理。

场景编辑器添加的I/II类船舶参与规则运动和观测，AIS打开时广播带噪位置，关闭后只有实际传感覆盖才能更新接触。I类AIS固定开启，II类可切换。这些是可选水面背景目标，默认任务仍聚焦水下UUV；AIS号码是明确标记的演示编号，不对应真实船舶。

第一次演示建议选择“辅助”，在对话中发送“将8艘UUV分组开展区域搜索，发现目标后提交跟踪方案”。任务计算成功后点击仿真开始。常规搜索可自动执行；跟踪和重搜索等待人工审批。也可在“请求”模式下逐项批准全部新计划。

等待模型或审批不会暂停已授权任务。缺少授权的艇处于非活动状态。保护性暂停、人工暂停与 PI 取消是不同操作；保护性暂停只允许人手动恢复。

## 实际架构

```text
ui/                 HTTP / WebSocket
  |
uuv_game/api.py      公共 API、内部工具接口、串行决策队列
  |                     ^
runtime.py           tools/pi/worker.ts
  |                  PI SDK + LongCat + Extension + 受信 Skill
  |
algorithms/         纯 Python 候选计算和曲率控制
  |
SQLite              检查点、回放、幂等回执、历史计划
```

Python 是状态权威，Node 只负责 PI 决策回合。模型不逐帧驱动运动。后台进程由启动器监护，浏览器关闭不终止仿真；程序崩溃后从检查点恢复，不补演离线期间的运动。检查点周期为 1 秒，因此进程崩溃可能损失最后不到一个周期的遥测，已提交的计划和回执则使用同一事务。

计算使用后台线程及只读快照，在返回时再次检查 episode。线程不是实时隔离保障；复杂规划受节点预算约束。普通 API 不无限等待，浏览器默认超时 10 秒。极端请求可能超时，需要重新查询状态或缩小规划范围；本版没有把每个算法封装为远端可取消计算进程。

## 九个 Tool

| Tool | 职责 | 算法/状态来源 |
| --- | --- | --- |
| `get_mission_state` | 读取当前任务、己方状态、观测接触、授权 | 后端快照，不含目标真值 |
| `get_observations` | 查询有界观测记录 | 带噪位置传感记录 |
| `compute_task_allocation` | 返回候选编队和默认搜索分区 | SciPy 槽位线性分配，最多3艇 |
| `plan_path` | 一艘艇的起终姿态路径 | Dubins六族、仅前进的有界姿态格搜索 |
| `plan_search` | 搜索或重搜索候选 | 条带扫描、Dubins连接、闭合重访航线 |
| `plan_tracking` | 已确认接触的跟踪策略 | 目标估计位置附近的距离带巡航 |
| `evaluate_plan` | 检查候选是否仍可执行、是否需审批 | 成员、几何、状态和权限校验 |
| `submit_mission_plan` | 唯一常规执行入口 | 不可变计划、幂等命令、权限门 |
| `get_action_status` | 查询候选、计划或决策作业 | 实际状态，不把接受当作完成 |

Extension 注册位于 `tools/pi/extension.ts`。SDK 使用显式九工具白名单，没有 shell、写文件或任意网络工具。工具参数经私有 HTTP 传给 Python；每次请求绑定运行中的 `run_id` 和 `episode_id`。取消或租约过期后拒绝迟到的执行请求。

算法标识：`dubins_hybrid`、`strip_coverage`、`slot_assignment`、`distance_band`，分别只能用于对应工具；`default` 选择对应默认算法。替换算法应保持字典接口、单位、失败语义和副作用边界，不能让计算函数直接操纵仿真器。

`plan_path` 是几何实验工具，不能单独提交执行：前向恒速艇到达普通路径末端不能直接停车。可执行搜索使用闭合路径；跟踪失效且没有已授权续行动作时保护性暂停。

## Skill 与权限

Skill 位于 `.pi/skills/multi-uuv-recon-tracking/SKILL.md`，只描述观察、选择工具、评估、提交、确认和失败处理。Worker 显式加载这一份受信 Skill，关闭工作目录的其他自动扩展发现；修改后重启 worker 才应用新内容。参考文档用于开发者维护，不会在没有读取工具的情况下假装自动加载。

权限规则由 Python 执行，不靠模型自觉遵守：Request 要求全部新计划审批；Assisted 搜索风险分0.2自动执行，跟踪/重搜索0.65超过阈值0.55而需审批；Full 跳过人工审批但不跳过硬检查。这些分数是演示分类规则，不是统计事故概率。

审批显示搜索区及单独的 `execution_domain`。后者包含完整转场路径包络，通常大于搜索 bbox；跟踪授权域是整张地图。活动控制始终检查批准的域。审批窗口为600仿真秒；已激活的计划持续有效，直到被替换、停止或安全检查失败。搜索循环属于原计划，不需要每圈重新批准。

## LongCat 配置

- 兼容接口：`https://api.longcat.chat/openai/v1`。
- 默认模型：`LongCat-2.0`，可通过 `LONGCAT_MODEL` 显式覆盖。
- `openai-completions` 工具协议，关闭 thinking，输出上限4096 tokens。
- 单回合最多24次工具调用，90秒墙钟截止；回合间至少15秒冷却。
- 工作租约15秒，每3秒心跳；失联作业标记失败，不盲目重放已经提交的动作。
- 周期复查每30仿真秒，事件合并，最多12个排队/运行作业；拥塞不会终止仿真。

官方接口与工具说明：[API 文档](https://longcat.chat/platform/docs/zh/api-docs)、[工具调用](https://longcat.chat/platform/docs/zh/tools-overview)。SDK中的费用系数暂设0，仅用于关闭不可靠估算，不代表模型免费；实际费用以服务平台账单为准。

## 验证

```sh
PYTHONPATH=tools python -m pytest tools/tests -q
node --import ./packages/coding-agent/src/experimental/source-resolver.ts --test tools/pi/config.test.ts tools/pi/session.test.ts
npx tsc -p tools/pi/tsconfig.json --noEmit
node --test ui/src/state/missionState.test.js
npm run check
```

真实模型验收会请求计费API，单独执行：

```sh
python tools/scripts/model_acceptance.py
python tools/scripts/model_acceptance.py --mission
PYTHONPATH=tools python tools/scripts/soak.py --seconds 1800
```

模型脚本默认读取启动器记录的后端地址，也可用 `--api` 指定。验收检查真实后端工具回执，不接受看起来像工具调用的文本。测试报告与截图在忽略目录 `tools/artifacts/`，实际结果见 `ACCEPTANCE.md`。

## 边界与维护

这是游戏演示，不是水动力仿真或真实装备控制系统。固定4km地图、4m/s前向巡航、60m最小半径；噪声观测和速度平滑不是完整声学模型或Kalman关联器。没有训练RL、完整MPC、复杂编队MILP或多目标身份关联。源码和 [algorithm-report.md](algorithm-report.md) 明确各方法适用范围；论文出处保留在 [DESIGN.md](DESIGN.md#15-文献依据与工程边界)。

逐艇规划不等于全局无冲突调度；运行时短时避碰不可行就暂停，不承诺任意场景永不暂停或始终跟踪成功。目标真值只用于生成观测和独立碰撞保护，不参与控制转向。

内存保留最近100个候选、100个作业/消息、500条观测、5000条事件及32个结束计划；待审批最多32个。回放每局最多7200帧、最多8局。历史计划和幂等回执单独写SQLite，不随每帧复制到内存；它们与PI会话是磁盘档案，长期部署仍需备份、磁盘监控和维护。30分钟验收不能证明永久无故障。

服务仅监听本机回环地址，使用浏览器HttpOnly cookie和独立worker令牌隔离操作入口。这不是公网多用户认证系统，不应直接暴露到外网。
