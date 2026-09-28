export const PRIORITY_COLORS = {
  high: "#DC2626",
  medium: "#D97706",
  low: "#2563EB",
};

export const UAV_STATUS_COLORS = {
  searching: "#0F766E",
  tracking: "#BE123C",
  returning: "#C2410C",
  holding: "#A16207",
  idle: "#475569",
  transit: "#1D4ED8",
  failed: "#7F1D1D",
};

export const OWNER_COLORS = ["#36D9A0", "#F3C845", "#4BBBEF", "#FA836A", "#B398F1", "#30C9D0", "#EA82B3", "#A4D74F"];

export function ownerColor(id) {
  const number = Number(String(id || "").match(/\d+/)?.[0]);
  return number ? OWNER_COLORS[(number - 1) % OWNER_COLORS.length] : "#94A3B8";
}

export function markerColor(ageMinutes) {
  if (ageMinutes < 15) return { fill: "#EA580C", alpha: 1 };
  if (ageMinutes < 45) return { fill: "#CA8A04", alpha: 0.86 };
  return { fill: "#854D0E", alpha: 0.64 };
}
