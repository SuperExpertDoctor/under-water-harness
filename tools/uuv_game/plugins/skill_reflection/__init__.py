"""Skill reflection plugin — distills scheduling trajectories into
reusable skills (extension category: toggleable, not removable).

Observer plugin: it never mutates the pipeline. A custom stage after
coverage_review periodically enqueues a reflection job for the PI agent
(LLM distillation over the window's decision trajectory + metric
delta); two tools expose the surface — ``skill_reflection__lookup`` for
lazy on-demand skill loading with bandit feedback, and
``skill_reflection__distill`` for the agent to commit/refine entries.
Skill categories are declared in src/impl.py CATEGORIES; disabling this
plugin removes both tools, the stage, and the /api/skills surface.
"""

from .src import impl

PLUGIN = {
    "id": "skill-reflection",
    "name": "技能凝练",
    "layer": 4,
    "color": "#a855f7",
    "desc": "将调度决策轨迹与任务指标经 LLM 反思凝练为技能库，并支持惰性加载与置信度反馈",
    "snippet": "Distills decision trajectories into a reusable skill library",
    "guidelines": [
        "扩展功能插件：可启停不可删除；关闭后技能凝练/查询/界面功能整体停用",
        "凝练任务经 runtime.queue_agent 走正常 agent 作业通道，不占用 tick",
        "技能类目固定在 src/impl.py CATEGORIES 注册表，distill 拒绝未声明类目",
    ],
    "inputs": "none",
    "outputs": "none",
    "core": False,
    "category": "extension",
    "edges": [{"from": "skill-reflection", "to": "uuv-control", "label": "经验指导"}],
}

activity = impl.activity
EDGE_SUBJECTS = {"skill-reflection>uuv-control": lambda L: L.get("moving", [])}


def tick_stages(runtime):
    return impl.tick_stages(runtime)


def _lookup(runtime, params, worker=False):
    return impl.skill_lookup(runtime, params, worker)


def _distill(runtime, params, worker=False):
    return impl.skill_distill(runtime, params, worker)


TOOLS = {
    "skill_reflection__lookup": {
        "description": "查询凝练技能库：skill_name 为空返回按置信度排序的技能目录（名称、类目、一句话描述、置信度、版本）；传入名称返回该技能正文与元数据，并记录一次使用（带上当前任务指标基线用于置信度反馈）。",
        "snippet": "Look up distilled mission skills on demand",
        "guidelines": [
            "当前计划涉及某类功能目标（目标调派/搜索转跟踪/跟踪交接/丢失重捕）时，先调用本工具拉取对应技能全文再行动",
            "skill_name 留空可浏览技能目录",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {"type": "string",
                    "description": "技能 slug 或标题；留空返回技能目录"},
            },
            "required": [],
            "additionalProperties": False,
        },
        "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
        "execution_mode": "sequential",
        "errors": ["skill_not_found", "invalid_params"],
        "execute": _lookup,
    },
    "skill_reflection__distill": {
        "description": "提交一条凝练技能（slug 已存在则视为 refine，版本号+1）。category 必须是注册表中的任务目标类目；库满时自动淘汰置信度最低的条目。",
        "snippet": "Commit or refine one distilled skill",
        "guidelines": [
            "仅在反思任务触发或确证了可复用协作模式时调用；不要把常规计划内容写进技能",
            "category 取值为 target-dispatch / search-to-track / track-handover / lost-reacquire",
        ],
        "parameters": {
            "type": "object",
            "properties": {
                "slug": {"type": "string", "minLength": 1,
                    "description": "kebab-case 技能标识；同名即 refine"},
                "title": {"type": "string", "minLength": 1},
                "category": {"type": "string", "minLength": 1},
                "description": {"type": "string", "minLength": 1,
                    "description": "一句话用途（展示于技能库列表）"},
                "body_md": {"type": "string", "minLength": 1,
                    "description": "Markdown 正文：经验内容、适用条件、槽位协作链"},
            },
            "required": ["slug", "title", "category", "description", "body_md"],
            "additionalProperties": False,
        },
        "constrained_sampling": {"type": "json_schema", "strict": "prefer"},
        "execution_mode": "sequential",
        "errors": ["skill_fields_required", "unknown_skill_category", "invalid_skill_slug", "invalid_params"],
        "execute": _distill,
    },
}
