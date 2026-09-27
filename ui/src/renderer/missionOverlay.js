import { routeToCells } from "../state/missionState";

export function drawMissionOverlay(context, frame, candidate, layout) {
  if (!frame?.task_area) return;
  const { cellSize, offsetX, offsetY, gridCols, gridRows } = layout;
  const colors = ["#0f766e", "#b45309", "#be123c"];
  context.save();
  context.beginPath();
  context.rect(offsetX, offsetY, gridCols * cellSize, gridRows * cellSize);
  context.clip();
  for (const bearing of frame.bearing_lines || []) {
    if (!bearing.from || !bearing.to) continue;
    context.beginPath();
    context.strokeStyle = "#b45309aa";
    context.lineWidth = 1;
    context.setLineDash([5, 5]);
    context.moveTo(offsetX + (bearing.from[0] + .5) * cellSize, offsetY + (bearing.from[1] + .5) * cellSize);
    context.lineTo(offsetX + (bearing.to[0] + .5) * cellSize, offsetY + (bearing.to[1] + .5) * cellSize);
    context.stroke();
  }
  if (candidate?.episode_id === frame.episode_id) {
    Object.entries(candidate.routes || {}).forEach(([, points], index) => {
      context.beginPath();
      context.strokeStyle = colors[index % colors.length];
      context.lineWidth = 2;
      context.setLineDash([5, 4]);
      routeToCells(points, frame.task_area).forEach(([col, row], pointIndex) => {
        const x = offsetX + (col + .5) * cellSize;
        const y = offsetY + (row + .5) * cellSize;
        if (pointIndex === 0) context.moveTo(x, y);
        else context.lineTo(x, y);
      });
      context.stroke();
    });
  }
  context.setLineDash([]);
  (frame.teams || []).forEach((team, index) => {
    context.strokeStyle = colors[index % colors.length];
    context.lineWidth = 2;
    for (const uuv of frame.uavs || []) {
      if (!team.members.includes(uuv.id)) continue;
      context.beginPath();
      context.arc(offsetX + (uuv.position[0] + .5) * cellSize, offsetY + (uuv.position[1] + .5) * cellSize, Math.max(8, cellSize * .6), 0, Math.PI * 2);
      context.stroke();
    }
  });
  for (const contact of frame.contacts || []) {
    if (!contact.estimated_position || !Number.isFinite(contact.uncertainty_m)) continue;
    const scale = cellSize / (frame.task_area.cell_size_km * 1000);
    const radius = contact.uncertainty_m * scale;
    context.beginPath();
    const x = offsetX + (contact.estimated_position[0] + .5) * cellSize;
    const y = offsetY + (contact.estimated_position[1] + .5) * cellSize;
    const covariance = contact.covariance;
    if (Array.isArray(covariance?.[0]) && Number.isFinite(covariance[1]?.[1])) {
      const a = covariance[0][0];
      const b = covariance[0][1];
      const d = covariance[1][1];
      const spread = Math.sqrt((a - d) ** 2 + 4 * b * b);
      const major = Math.sqrt(Math.max(0, (a + d + spread) / 2)) * 2 * scale;
      const minor = Math.sqrt(Math.max(0, (a + d - spread) / 2)) * 2 * scale;
      context.ellipse(x, y, Math.max(3, major), Math.max(3, minor), -.5 * Math.atan2(2 * b, a - d), 0, Math.PI * 2);
    } else context.arc(x, y, Math.max(3, radius), 0, Math.PI * 2);
    context.fillStyle = "#b4530914";
    context.strokeStyle = "#b45309";
    context.lineWidth = 1;
    context.setLineDash([3, 4]);
    context.fill();
    context.stroke();
  }
  context.restore();
}
