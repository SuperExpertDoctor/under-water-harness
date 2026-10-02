# 协同目标跟踪（`coop-tracking`）

多艇前视被动声纳在协同区内编队稳定跟踪。多艇在协同区内按被动方位编队稳定跟踪

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `track_leases`
- `handover_prep`
- `motion`
- `observations`

## 连接边

- `coop-tracking` -> `reacquire`
- `coop-tracking` -> `uuv-control`

## 使用说明

- 成员到位且几何达标后整队切换被动模式
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
