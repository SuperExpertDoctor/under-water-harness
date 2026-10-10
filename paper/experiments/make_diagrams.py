"""Vector architecture diagrams (PDF) for the paper: method framework + system."""
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
import matplotlib.font_manager as fm

plt.rcParams["font.family"] = "Noto Sans CJK SC"
plt.rcParams["pdf.fonttype"] = 42


def box(ax, xy, w, h, text, fc="#e8f0fa", ec="#2f5f8f", fs=8.5, lw=1.0):
    x, y = xy
    ax.add_patch(FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.012",
                              fc=fc, ec=ec, lw=lw))
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, linespacing=1.35)


def arrow(ax, p1, p2, text="", fs=7.5, style="-|>", color="#333",
          connectionstyle="arc3,rad=0"):
    ax.add_patch(FancyArrowPatch(p1, p2, arrowstyle=style, color=color,
                               mutation_scale=11, lw=1.1,
                               connectionstyle=connectionstyle))
    if text:
        mx, my = (p1[0] + p2[0]) / 2, (p1[1] + p2[1]) / 2
        ax.text(mx, my + 0.014, text, ha="center", va="bottom",
                fontsize=fs, color="#333")


def fig_arch():
    fig, ax = plt.subplots(figsize=(7.6, 4.2))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    # LLM semantic compiler band
    box(ax, (0.03, 0.78), 0.30, 0.17, "情报通报\n（文本）", fc="#fdf0e6", ec="#a05a20")
    box(ax, (0.03, 0.50), 0.30, 0.17, "指挥意图 + 实时态势\n（文本+数值）", fc="#fdf0e6", ec="#a05a20")
    box(ax, (0.42, 0.60), 0.26, 0.26, "LLM 语义编译器\nJSON Schema 约束\n界钳制·回退", fc="#fdeef0", ec="#a02350", fs=9)
    box(ax, (0.76, 0.78), 0.21, 0.17, "目标存在先验\nπ(x)", fc="#eef7ea", ec="#3a7a35")
    box(ax, (0.76, 0.50), 0.21, 0.17, "采集策略参数\nλ（有界）", fc="#eef7ea", ec="#3a7a35")
    arrow(ax, (0.33, 0.865), (0.42, 0.78))
    arrow(ax, (0.33, 0.585), (0.42, 0.66))
    arrow(ax, (0.68, 0.80), (0.76, 0.86), "编译")
    arrow(ax, (0.68, 0.66), (0.76, 0.585), "编译")
    # AL-DNN loop
    box(ax, (0.06, 0.24), 0.24, 0.16, "先验引导\n初始布点 DoE", fc="#e8f0fa", ec="#2f5f8f", fs=9)
    box(ax, (0.40, 0.24), 0.26, 0.16, "信念图 DNN 增量拟合\n置信边界 / $\hat{P}_s$", fc="#e8f0fa", fs=9)
    box(ax, (0.76, 0.24), 0.21, 0.16, "双侧采样\n确认/排除任务", fc="#e8f0fa", fs=9)
    box(ax, (0.40, 0.02), 0.26, 0.13, "多艇批量探测\n（真实调用）", fc="#dfeaf7", ec="#2f5f8f", fs=9)
    box(ax, (0.06, 0.02), 0.24, 0.13, "收敛判定\nε ≤ 0.02", fc="#dfeaf7", ec="#2f5f8f", fs=9)
    arrow(ax, (0.86, 0.78), (0.30, 0.40), "", connectionstyle="arc3,rad=-0.25")
    ax.text(0.62, 0.47, "先验注入", fontsize=7.5, ha="center")
    arrow(ax, (0.865, 0.50), (0.92, 0.40), "", connectionstyle="arc3,rad=0.2")
    ax.text(0.945, 0.44, "参数注入", fontsize=7.5, ha="center")
    arrow(ax, (0.30, 0.32), (0.40, 0.32))
    arrow(ax, (0.66, 0.32), (0.76, 0.32))
    arrow(ax, (0.86, 0.24), (0.60, 0.15), "", connectionstyle="arc3,rad=-0.15")
    arrow(ax, (0.40, 0.085), (0.30, 0.085))
    arrow(ax, (0.18, 0.15), (0.18, 0.24), "", connectionstyle="arc3,rad=-0.2")
    ax.text(0.05, 0.19, "未收敛\n反馈", fontsize=7.5, ha="center")
    fig.savefig("../latex/fig_arch.pdf", bbox_inches="tight")
    fig.savefig("../latex/fig_arch.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def fig_sys():
    fig, ax = plt.subplots(figsize=(7.6, 4.4))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    # three layers
    for y, name, fc in [(0.72, "语义编排层", "#fdf0e6"),
                        (0.38, "信念推理层", "#eef4fb"),
                        (0.04, "执行控制层", "#eef7ea")]:
        ax.add_patch(FancyBboxPatch((0.02, y), 0.96, 0.26,
                                    boxstyle="round,pad=0.008",
                                    fc=fc, ec="#666", lw=1.0, alpha=0.55))
        ax.text(0.045, y + 0.215, name, fontsize=9.5, fontweight="bold",
                color="#333")
    box(ax, (0.14, 0.755), 0.30, 0.14, "LLM 语义编译服务\n情报→先验 / 意图→参数", fc="#fff", ec="#a05a20")
    box(ax, (0.56, 0.755), 0.32, 0.14, "人机接口\n三档权限 · 决策理由留痕", fc="#fff", ec="#a05a20")
    box(ax, (0.08, 0.415), 0.28, 0.14, "信念图引擎\n增量 DNN · $\hat{P}_s$ 估计", fc="#fff", ec="#2f5f8f")
    box(ax, (0.40, 0.415), 0.26, 0.14, "双侧采样调度\n确认 / 排除任务生成", fc="#fff", ec="#2f5f8f")
    box(ax, (0.70, 0.415), 0.24, 0.14, "证据老化剔除\nΔ 阈值 · 龄期约束", fc="#fff", ec="#2f5f8f")
    box(ax, (0.10, 0.07), 0.30, 0.14, "多艇任务分配\nCBS 冲突消解", fc="#fff", ec="#3a7a35")
    box(ax, (0.44, 0.07), 0.26, 0.14, "路径规划\nDubins 约束", fc="#fff", ec="#3a7a35")
    box(ax, (0.74, 0.07), 0.20, 0.14, "探测模型\n机动目标场", fc="#fff", ec="#3a7a35")
    arrow(ax, (0.29, 0.755), (0.22, 0.555))
    arrow(ax, (0.72, 0.755), (0.72, 0.555))
    arrow(ax, (0.22, 0.415), (0.25, 0.21))
    arrow(ax, (0.53, 0.415), (0.57, 0.21))
    arrow(ax, (0.82, 0.415), (0.84, 0.21))
    arrow(ax, (0.84, 0.21), (0.36, 0.47), "观测回传",
          connectionstyle="arc3,rad=0.35")
    fig.savefig("../latex/fig_sys.pdf", bbox_inches="tight")
    fig.savefig("../latex/fig_sys.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


if __name__ == "__main__":
    fig_arch(); fig_sys()
    print("diagrams written")
