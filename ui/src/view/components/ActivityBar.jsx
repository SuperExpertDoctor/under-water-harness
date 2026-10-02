import { VIEW_DEFS } from "../../state/viewRegistry";

// VSCode-style activity bar: narrow left rail switching between registered
// main views. Views stay mounted (map keeps its export ref alive); CSS
// `view-<id>` classes on .app-layout decide which one is visible.
export default function ActivityBar({ activeView, onSelect }) {
  return (
    <nav className="activity-bar" aria-label="视图切换">
      {VIEW_DEFS.map((view) => {
        const Icon = view.icon;
        const active = activeView === view.id;
        return (
          <button
            key={view.id}
            type="button"
            className={`activity-btn ${active ? "active" : ""}`}
            title={view.title}
            aria-label={view.title}
            aria-pressed={active}
            onClick={() => onSelect(view.id)}
          >
            <Icon size={19} />
          </button>
        );
      })}
    </nav>
  );
}
