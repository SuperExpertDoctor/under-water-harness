# 能源轮换（`energy-lifecycle`）

油量监视、低油量返航、替补艇生成与跟踪交接。监视能源并驱动低油量艇返航与替补轮换

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `exit_prep`
- `motion`

## 连接边

- `energy-lifecycle` -> `task-allocation`
- `energy-lifecycle` -> `path-planning`
- `energy-lifecycle` -> `region-partition`（常亮）

## 使用说明

- 退场艇在交接窗口内优先等待接替艇到位
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
