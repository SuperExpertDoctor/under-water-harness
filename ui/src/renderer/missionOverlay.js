import { routeToCells } from "../state/missionState";

export function drawMissionOverlay(context, frame, candidate, layout) {
  if (!frame?.task_area) return;
  const { cellSize, offsetX, offsetY, gridCols, gridRows } = layout;
  const colors = ["#0f766e", "#b45309", "#be123c"];
  context.save();
  context.beginPath();
  context.rect(offsetX, offsetY, gridCols * cellSize, gridRows * cellSize);
  context.clip();
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
    const radius = contact.uncertainty_m / (frame.task_area.cell_size_km * 1000) * cellSize;
    context.beginPath();
    context.arc(offsetX + (contact.estimated_position[0] + .5) * cellSize, offsetY + (contact.estimated_position[1] + .5) * cellSize, Math.max(3, radius), 0, Math.PI * 2);
    context.fillStyle = "#b4530914";
    context.strokeStyle = "#b45309";
    context.lineWidth = 1;
    context.setLineDash([3, 4]);
    context.fill();
    context.stroke();
  }
  context.restore();
}
