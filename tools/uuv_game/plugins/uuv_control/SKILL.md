# UUV 平台控制（`uuv-control`）

航向 / 速度 / 传感器模式执行与平台遥测。执行航向、速度与传感器模式的平台指令

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`one`

## 承担的流水线槽位

- `motion`

## 连接边

- 无

## 使用说明

- 所有插件的运动意图最终经此输出到平台
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
