# 动态任务区域划分（`region-partition`）

把搜索海域划为责任区，随能源与覆盖动态修补。按覆盖时效与目标线索动态重划责任区

## 端口

- 输入端子：`one`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `contact_repairs`
- `coverage_review`

## 连接边

- `region-partition` -> `task-allocation`

## 使用说明

- 执行中的责任区保持边界，只重划无属水域
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
