# UUV-display-ui

独立的多 UUV 实时态势显示前端。原项目仅作为界面和数据格式参考，本项目不依赖原项目运行时算法。

## 功能

- 接收原项目格式的 WebSocket 实时帧；
- 使用 `uavs[].position` 更新 UUV 位置；
- 使用 `uavs[].trail` 显示轨迹；
- 使用 `info_matrix` 和 `value_matrix` 刷新地图颜色；
- 全局保存后端最新完整信息场，支持后端省略矩阵时继续显示上一份矩阵；
- 地图、信息态势统计和网格悬停提示统一读取同一份信息场；
- 显示每个 UUV 的任务模式、运行状态、坐标和轨迹点数；
- 在 UUV 周围显示半径 `R=2` 个栅格的圆形扫描范围；
- 通过帧间插值让位置变化更平滑；
- UI 已迁移信息量本身的刷新机制：记录 UUV 扫描网格的最近扫描时间，并按搜索/跟踪半衰期重新计算 `info_matrix`；
- 保留任务操作入口：PI Agent、Skill、任务装配、算法调用、重点区和仿真控制；
- 前端命令只提交后端 API，不在浏览器内重复实现任务分配、路径规划或 LLM。

## 保留的接口

接口封装在 `src/api/`：

- `websocketApi.js`：默认连接 `/ws/live`，接收完整帧对象；
- `websocketApi.js`：同时预留 `/ws/state` 状态推送；
- `httpApi.js`：保留通用 GET、POST、PUT、PATCH、DELETE、配置和健康检查接口；
- `stateApi.js`：仿真状态和启动、暂停、重置接口；
- `eventApi.js`：事件列表、目标发现、目标丢失测试接口；
- `piAgentApi.js`：PI Agent 任务分配接口；
- `taskAssemblyApi.js`：任务装配接口；
- `skillApi.js`：Skill 统一接口；
- `algorithmApi.js`：保留任务规划、决策和算法命令调用边界；
- `index.js`：统一导出接口。

界面中的命令入口也保留在以下位置：

- `src/App.jsx`：场景对象命令和回放控制；
- `src/components/IntentPanel.jsx`：重点区创建、修改、取消和运行控制；
- `src/api/piAgentApi.js`：PI Agent 任务分配；
- `src/api/taskAssemblyApi.js`：任务装配；
- `src/api/skillApi.js`：Skill 执行；
- `src/api/algorithmApi.js`：算法规划、决策和命令调用。

默认后端端口为 `8765`，可以通过环境变量 `VITE_BACKEND_PORT` 修改。也可以使用 `VITE_WS_URL`、`VITE_TELEMETRY_WS_PATH`、`VITE_STATE_WS_PATH` 指定 WebSocket 地址。

## 接口清单

| 类型 | 方法 | 路径 | 用途 |
| --- | --- | --- | --- |
| 仿真状态 | GET | `/api/state` | 获取当前全部状态 |
| 仿真控制 | POST | `/api/simulation/start` | 启动仿真 |
| 仿真控制 | POST | `/api/simulation/pause` | 暂停仿真 |
| 仿真控制 | POST | `/api/simulation/reset` | 重置仿真 |
| 事件 | GET | `/api/events` | 获取事件日志 |
| 测试事件 | POST | `/api/test/target-detected` | 测试目标发现 |
| 测试事件 | POST | `/api/test/target-lost` | 测试目标丢失 |
| 状态推送 | WebSocket | `/ws/state` | 后端状态变化主动通知 UI |
| 轨迹帧 | WebSocket | `/ws/live` | 兼容原项目的连续仿真帧 |
| PI Agent | POST | `/api/pi-agent/task-assignment` | 提交任务分配请求 |
| 任务装配 | POST | `/api/task-assembly/assemble` | 提交任务装配请求 |
| Skill | POST | `/api/skills/{skillId}/execute` | 执行统一 Skill |

## 启动

```powershell
npm install
npm run dev
```

浏览器打开：

```text
http://127.0.0.1:5173/
```

## 数据边界

UI 直接读取后端原始字段，不修改后端 JSON 格式。主要读取字段：

```text
frame_id
timestamp
task_area
info_matrix
value_matrix
uavs
```

UI 不实现任务分配、LLM 决策、路径规划、油耗、返航和信息量衰减算法；
UI 只提交命令并显示后端返回的状态，实时态势来自后端 `uavs[]` 和 `info_matrix`。

### 全局信息场刷新

`src/state/informationField.js` 负责信息场的前端状态边界：

- 收到包含 `info_matrix` / `value_matrix` 的帧时，替换全局矩阵；
- 收到不含矩阵的轻量帧时，保留上一份完整矩阵；
- 同步保存 `information_version`、矩阵来源帧号和仿真时间；
- `CanvasMap`、`RightSidebar` 和网格悬停提示通过 `App.jsx` 使用同一份矩阵。

`src/state/informationField.js` 已包含信息量计算链路：

- UUV 处于搜索或跟踪状态时，优先使用后端公开的 `sar_footprint` 刷新网格；
- 没有 `sar_footprint` 时，使用圆形扫描半径 `R=2` 格刷新网格；
- 搜索网格使用 30 分钟半衰期，跟踪网格使用 15 分钟半衰期；
- 每一帧按 `exp(-ln(2) * age / half_life)` 重新计算 `info_matrix`；
- 后端矩阵只用于首次连接时初始化，之后由 UI 根据扫描时间重新计算。

因此独立 UI 现在包含“扫描刷新 -> 记录最近扫描时间 -> 时间衰减 -> 重新计算信息矩阵”的完整链路。
