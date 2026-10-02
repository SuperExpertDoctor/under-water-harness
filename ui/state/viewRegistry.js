// Registry of switchable main views. The ActivityBar renders one entry per
// view; adding a new view = append a def here, mount its component in App,
// and add a `view-<id>` visibility rule in mission-v2.css.
import { Map as MapIcon, Sparkles, Workflow } from "lucide-react";

export const VIEW_DEFS = [
  { id: "map", title: "态势地图", icon: MapIcon },
  { id: "plugins", title: "插件视图", icon: Workflow },
  { id: "skills", title: "技能视图", icon: Sparkles },
];
