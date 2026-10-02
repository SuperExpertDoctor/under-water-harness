# 任务分配（`task-allocation`）

把获批计划的成员 / 责任区指派到具体 UUV。把计划成员与责任区落实到具体 UUV

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `plan_lifecycle`

## 连接边

- `task-allocation` -> `coverage-search`
- `task-allocation` -> `path-planning`
- `task-allocation` -> `coop-tracking`
- `task-allocation` -> `reacquire`

## 使用说明

- 候选分配不产生运动，需经评估并提交后才执行
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
