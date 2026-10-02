# 失联再捕获（`reacquire`）

目标丢失后重搜该区域、再发现并恢复跟踪。目标丢失后重搜区域、再发现并恢复跟踪

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `contact_repairs`
- `motion`

## 连接边

- `reacquire` -> `coop-tracking`
- `reacquire` -> `uuv-control`

## 使用说明

- 再捕获搜索不占用跟踪计划名额
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
