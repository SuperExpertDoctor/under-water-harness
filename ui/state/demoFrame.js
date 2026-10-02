const DEMO_COLS = 20;
const DEMO_ROWS = 20;

function createMatrix(defaultValue = 0) {
  return Array.from(
    { length: DEMO_COLS },
    () => Array.from({ length: DEMO_ROWS }, () => defaultValue),
  );
}

function createDemoValueMatrix() {
  const matrix = createMatrix(0.04);
  for (let col = 8; col < 13; col += 1) {
    for (let row = 8; row < 13; row += 1) {
      matrix[col][row] = 0.35;
    }
  }
  return matrix;
}

export const DEMO_FRAME = {
  schema_version: "mission-frame/v2",
  visual_schema_version: "mission-visual/v1",
  frame_id: 0,
  episode_id: "local-demo",
  sim_time_min: 0,
  timestamp: "00:00:00",
  cycle: 0,
  runtime_status: "local_demo",
  task_area: {
    width_km: 100,
    height_km: 100,
    cell_size_km: 5,
  },
  info_matrix: createMatrix(0),
  value_matrix: createDemoValueMatrix(),
  searchable_cells: 400,
  coverage_pct: 0,
  base_position: [2, 10],
  bases: [
    {
      id: "Base-1",
      number: 1,
      position: [2, 10],
      capacity: 8,
      occupancy: 0,
      busy: false,
    },
  ],
  support_base_positions: [],
  obstacles: [
    {
      id: "demo-storm",
      type: "thunderstorm",
      center: [15, 14],
      size: 1,
    },
  ],
  search_regions: [
    {
      id: "SEARCH-DEMO",
      bbox: [8, 8, 13, 13],
      type: "search",
      status: "active",
      priority: "high",
      completion_pct: 0,
      assigned_uav_id: "UUV-1",
    },
  ],
  track_regions: [],
  contacts: [],
  ships: [],
  scenario_vessels: [],
  markers: [],
  evidence: [],
  passive_observations: [],
  passive_positions: [],
  intents: [],
  intent_statuses: [],
  intent_events: [],
  handoffs: [],
  events: [],
  llm_cycle: null,
  vessel_mutation_allowed: false,
  // Local-only snapshot: show the full eight-UUV formation before a backend
  // frame arrives. A real /ws/live frame replaces this entire list.
  uavs: [
    {
      id: "UUV-1",
      position: [5.5, 5.5],
      trail: [[3.5, 4.5], [4.5, 5.0], [5.5, 5.5]],
      status: "searching",
      operation_mode: "coverage",
      operational_status: "available",
      assigned_region_id: "SEARCH-DEMO",
      target_group_id: null,
      control_owner: "system",
      task_visual: { task_type: "coverage", phase: "scanning", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-2",
      position: [11.5, 5.5],
      trail: [[13.5, 4.5], [12.5, 5.0], [11.5, 5.5]],
      status: "transit",
      operation_mode: "coverage",
      operational_status: "available",
      assigned_region_id: "SEARCH-DEMO",
      target_group_id: null,
      control_owner: "system",
      transit_progress: 0.72,
      task_visual: { task_type: "coverage", phase: "transit", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-3",
      position: [14.5, 9.5],
      trail: [[14.5, 7.5], [14.5, 8.5], [14.5, 9.5]],
      status: "tracking",
      operation_mode: "track",
      operational_status: "available",
      assigned_region_id: null,
      target_group_id: "DEMO-TARGET",
      control_owner: "system",
      task_visual: { task_type: "track", phase: "tracking", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-4",
      position: [8.5, 10.5],
      trail: [[7.5, 12.5], [8.0, 11.5], [8.5, 10.5]],
      status: "searching",
      operation_mode: "coverage",
      operational_status: "available",
      assigned_region_id: "SEARCH-DEMO",
      target_group_id: null,
      control_owner: "system",
      task_visual: { task_type: "coverage", phase: "scanning", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-5",
      position: [12.5, 12.5],
      trail: [[10.5, 12.5], [11.5, 12.5], [12.5, 12.5]],
      status: "searching",
      operation_mode: "coverage",
      operational_status: "available",
      assigned_region_id: "SEARCH-DEMO",
      target_group_id: null,
      control_owner: "system",
      task_visual: { task_type: "coverage", phase: "scanning", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-6",
      position: [16.5, 6.5],
      trail: [[16.5, 4.5], [16.5, 5.5], [16.5, 6.5]],
      status: "transit",
      operation_mode: "coverage",
      operational_status: "available",
      assigned_region_id: "SEARCH-DEMO",
      target_group_id: null,
      control_owner: "system",
      transit_progress: 0.54,
      task_visual: { task_type: "coverage", phase: "transit", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-7",
      position: [6.5, 15.5],
      trail: [[4.5, 15.5], [5.5, 15.5], [6.5, 15.5]],
      status: "returning",
      operation_mode: "return",
      operational_status: "available",
      assigned_region_id: null,
      target_group_id: null,
      control_owner: "safety",
      home_base_grid: [2, 10],
      task_visual: { task_type: "return", phase: "return", route_source: "demo", route_status: "active" },
    },
    {
      id: "UUV-8",
      position: [17.5, 16.5],
      trail: [[17.5, 14.5], [17.5, 15.5], [17.5, 16.5]],
      status: "tracking",
      operation_mode: "track",
      operational_status: "available",
      assigned_region_id: null,
      target_group_id: "DEMO-TARGET",
      control_owner: "system",
      task_visual: { task_type: "track", phase: "tracking", route_source: "demo", route_status: "active" },
    },
  ],
};
