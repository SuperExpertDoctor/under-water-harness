# skills/ — 技能库目录

每个技能一个 `<slug>/SKILL.md` 文件夹；`index.json` 由存储层维护
（bandit 元数据、使用次数、版本、置信度），不要手工编辑。

## 人工添加

新建 `tools/skills/<slug>/SKILL.md`，可选 frontmatter：

```markdown
---
title: 低油轮换接力
category: track-handover
description: 一句话用途
---

# 正文（Markdown）
```

`category` 必须是 `tools/uuv_game/plugins/skill_reflection/src/impl.py`
CATEGORIES 中的类目，留空则按通用权重反馈。下次 `load()` 自动登记
（`source: "manual"`）。

## 自动凝练

`skill_reflection__distill` 工具写入同一布局（`source: "distilled"`），
同 slug 视为 refine（version+1），库满按置信度最低淘汰。

## 加载模式

`configs/uuv_game.json` `algorithms.skills`：
`load_mode: "scan"` 扫描全部文件夹（默认）；
`load_mode: "manual"` 只登记 `manual_skills` 列出的 slug。
