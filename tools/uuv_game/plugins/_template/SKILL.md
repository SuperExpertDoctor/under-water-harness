# 插件模板使用说明

一个 plugin 是一个**文件夹**：对外只暴露 `__init__.py` 一套接口，实现可拆任意多个 `src/` 模块。

## 结构

```
plugins/<my-plugin>/
  __init__.py      # PLUGIN 声明 + 钩子/TOOLS 再导出（唯一接口面）
  src/             # 内部实现：任意多个 .py，互相 import 自由
    __init__.py
    impl.py        # activity / EDGE_SUBJECTS / tick_stages / 工具体
  SKILL.md         # 本文件：插件用途、端口、槽位、用法
```

## 注册方式

1. 复制本目录为 `plugins/<name>/`（目录名不带 `_` 前缀），改 `PLUGIN.id` 为 kebab-case。
2. 重启 api（或下次 reload）即自动加载：`GET /api/plugins` 出现节点；`activity()` 上报的主体出现在连接图；`tick_stages()` 返回的自定义阶段插入 tick 流水线指定槽位之后。
3. `TOOLS` 声明的工具经注册审查（`agent_tools/review.py`）通过后进入 `GET /internal/agent/tools`，PI worker 的适配器自动转成 `registerTool()`——TS 侧零改动。

## 审查底线（不过则不注册）

- 必填参数缺失时 execute 必须抛错，不得静默返回
- `execution_mode` 显式声明；插件工具恒为 `sequential`（runtime.lock 下共享态排队）
- `constrained_sampling` 必须为 json_schema strict prefer|require
- `calls_model: true` 时结果必须带 `usage`
