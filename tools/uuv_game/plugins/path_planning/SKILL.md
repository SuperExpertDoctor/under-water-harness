# 协同路径规划（`path-planning`）

虚拟领航点轨迹：引导 UUV 到站位、责任区或出口。以虚拟领航点轨迹引导 UUV 到站位或区域

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `motion`

## 连接边

- `path-planning` -> `coverage-search`
- `path-planning` -> `coop-tracking`
- `path-planning` -> `uuv-control`

## 使用说明

- 转场途中目标进入主动窄波束仍会采样补盲
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
