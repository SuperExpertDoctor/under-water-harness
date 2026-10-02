# 区域覆盖搜索（`coverage-search`）

侧扫 + 前视主动声纳沿责任区扫线持续覆盖。沿责任区扫线持续覆盖并积累扫描时效

## 端口

- 输入端子：`one`（none=不接输入 / one=单艇 / many=多艇）
- 输出端子：`many`

## 承担的流水线槽位

- `motion`
- `coverage_review`

## 连接边

- `coverage-search` -> `coop-tracking`
- `coverage-search` -> `uuv-control`

## 使用说明

- 每艇只持有一个责任区，单输入端子
- 核心算法插件，不可关闭

## 结构

- `__init__.py`：对外接口——`PLUGIN` 声明 + 钩子再导出
- `src/impl.py`：activity / EDGE_SUBJECTS / STAGES 实现
- 自定义插件可另加 `TOOLS`（经注册审查后进入 PI 工具目录）
