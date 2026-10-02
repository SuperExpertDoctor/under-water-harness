# skill-reflection — 技能凝练（扩展功能插件）

将一段时间的调度决策轨迹与任务指标结合，经 PI agent 的 LLM 反思凝练为
可复用技能库；技能按"功能目标"分类目，惰性加载，置信度经多臂老虎机反馈。

## 接口（对外唯一暴露面 = 本目录 `__init__.py`）

- `PLUGIN`：category `"extension"` — 可启停、不可删除（区别于 core 锁灰与
  custom 可删）。关闭后：两个工具从目录消失、反思 stage 跳过、/api/skills 显示停用。
- `TOOLS`：
  - `skill_reflection__lookup` — 惰性加载面。`skill_name` 为空 → 按 UCB 排序的
    技能目录（slug/标题/类目/描述/置信度/版本）；传入名称 → 技能正文+元数据，
    并记录使用时刻的指标基线（后续 settle 时结算 reward）。
  - `skill_reflection__distill` — 凝练提交。必填 slug/title/category/
    description/body_md；`category` 必须 ∈ `CATEGORIES` 注册表；同 slug 视为
    refine（version+1）；库满按置信度最低淘汰。
- `tick_stages`：在 `coverage_review` 槽位后追加反思 stage——每 `window_s`
  结算 pending reward 并 `queue_agent` 一条反思作业（source=skill_reflection），
  作业文本含窗口轨迹摘要+指标差量+类目表+待 refine 清单。
- `activity`：面板显示技能库规模/反思进行中。

## 类目注册表（src/impl.py `CATEGORIES`）

| id | 功能目标 | 槽位协作链 | reward 侧重 |
|----|----------|-----------|-------------|
| target-dispatch | 目标发现资源调派 | sensor_fusion→task_allocation→coop_tracking | 有效跟踪/失联 |
| search-to-track | 覆盖搜索→协同跟踪 | coverage_search→sensor_fusion→task_allocation→coop_tracking | 跟踪+覆盖 |
| track-handover | 跟踪轮换交接 | energy_lifecycle→handover→reacquire→coop_tracking | 失联+交接成功 |
| lost-reacquire | 丢失→重捕 | reacquire→coverage_search→sensor_fusion→coop_tracking | 失联+覆盖 |

凝练以**槽位/功能名**为节点而非 plugin id —— 同类功能的多个插件实现
替换后技能仍然成立。

## 存储

`tools/skills/`（算法目录）：每技能一个 `<slug>/SKILL.md` 文件夹 +
`index.json` 元数据（Beta 后验 alpha/beta、confidence、uses、version、
pending 使用基线、rewards 历史、library_size 覆盖值、source 标记）。

**两种入库方式**：人工 —— 在 `tools/skills/` 下放 `<slug>/SKILL.md`
（可选 `---` frontmatter 声明 `title`/`category`/`description`），
下次 `load()` 自动登记；自动 —— `skill_reflection__distill` 写同一
布局。初始加载由 configs `algorithms.skills.load_mode` 控制：
`scan` 扫描全部 SKILL.md 文件夹，`manual` 只登记 `manual_skills`
列出的 slug。

## 配置（configs/uuv_game.json `algorithms.skills`）

`library_size`（默认 10，界面可调，覆盖值存 index.json）、`window_s`
（反思周期）、`reward_window_s`（使用后指标观察窗）、
`refine_confidence_floor`、`reflect_max_chars`。

## Bandit

`src/bandit.py`：使用后置 reward = 类目权重加权的指标差量（覆盖率/
有效跟踪/失联/交接成功率，映射到 [0,1]）→ Beta 后验更新 confidence；
目录排序用 UCB（confidence + 探索项）。
