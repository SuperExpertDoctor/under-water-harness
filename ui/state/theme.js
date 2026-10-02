import { useEffect, useState } from "react";

// "light" | "dark" | "auto" — auto follows local time: dark between
// 18:00 and 07:00 next day, light otherwise. Preference persists in
// localStorage; the resolved scheme is written to <html data-theme>.
const MODES = ["light", "dark", "auto"];
const KEY = "uuv-theme";

function autoMode(now = new Date()) {
  const hour = now.getHours();
  return hour >= 18 || hour < 7 ? "dark" : "light";
}

function resolve(mode, now) {
  return mode === "auto" ? autoMode(now) : mode;
}

export default function useTheme() {
  const [mode, setMode] = useState(() => {
    try {
      const saved = localStorage.getItem(KEY);
      return MODES.includes(saved) ? saved : "light";
    } catch {
      return "light";
    }
  });
  const [resolved, setResolved] = useState(() => resolve(mode));

  useEffect(() => {
    try {
      localStorage.setItem(KEY, mode);
    } catch { /* storage unavailable — theme stays session-local */ }
    const apply = () => {
      const next = resolve(mode);
      setResolved(next);
      document.documentElement.dataset.theme = next;
    };
    apply();
    // auto mode needs re-evaluation across the 07:00/18:00 boundaries
    const timer = mode === "auto" ? setInterval(apply, 60 * 1000) : null;
    return () => { if (timer) clearInterval(timer); };
  }, [mode]);

  const cycle = () => setMode((m) => MODES[(MODES.indexOf(m) + 1) % MODES.length]);
  return { mode, resolved, cycle };
}
