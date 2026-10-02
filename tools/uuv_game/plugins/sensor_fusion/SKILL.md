# 多艇方位融合（`sensor-fusion`）

≥2 艇前视被动方位观测经 EKF 融合为目标估计。把多艇被动方位观测融合为目标估计

## 端口

- 输入端子：`many`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`one`

## 承担的流水线槽位

- `observations`

## 连接边

- `sensor-fusion` -> `coop-tracking`

## 使用说明

- 单艇方位只给方向线，稳定跟踪需要几何达标的双艇以上
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
