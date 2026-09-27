import { Type, type TSchema } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { TOOL_NAMES } from "./config.ts";

const snapshot = {
  episode_id: Type.Optional(Type.String({ minLength: 1 })),
  mission_revision: Type.Optional(Type.Integer({ minimum: 0 })),
};
const bbox = Type.Optional(Type.Array(Type.Number({ minimum: 0, maximum: 4000 }), { minItems: 4, maxItems: 4, description: "[xmin,ymin,xmax,ymax] in meters, not UI cells." }));
const fleet = Type.Optional(Type.Array(Type.String({ minLength: 1 }), { minItems: 1, maxItems: 8, uniqueItems: true }));
function algorithm(id: string) {
  return Type.Optional(Type.Union([Type.Literal("default"), Type.Literal(id)]));
}
const schemas: Record<string, TSchema> = {
  get_mission_state: Type.Object({}, { additionalProperties: false }),
  get_observations: Type.Object({ after_s: Type.Optional(Type.Number({ minimum: 0 })), cursor: Type.Optional(Type.Integer({ minimum: 0 })), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })) }, { additionalProperties: false }),
  partition_search_area: Type.Object({ ...snapshot, algorithm_id: algorithm("connected_partition"), members: fleet }, { additionalProperties: false }),
  compute_task_allocation: Type.Object({ ...snapshot, algorithm_id: algorithm("slot_assignment"), contact_id: Type.Optional(Type.String({ minLength: 1 })), tasks: Type.Optional(Type.Array(Type.Object({ id: Type.String({ minLength: 1 }), center: Type.Array(Type.Number(), { minItems: 2, maxItems: 2 }), size: Type.Integer({ minimum: 1, maximum: 3 }), priority: Type.Number({ minimum: 1, maximum: 10 }) }, { additionalProperties: false }), { maxItems: 8 })) }, { additionalProperties: false }),
  plan_path: Type.Object({ ...snapshot, algorithm_id: algorithm("dubins_hybrid"), members: Type.Array(Type.String({ minLength: 1 }), { minItems: 1, maxItems: 1 }), goal: Type.Array(Type.Number(), { minItems: 3, maxItems: 3, description: "[x_m,y_m,heading_rad]." }) }, { additionalProperties: false }),
  plan_search: Type.Object({ ...snapshot, algorithm_id: algorithm("strip_coverage"), members: fleet, bbox, mode: Type.Optional(Type.Union([Type.Literal("search"), Type.Literal("reacquire")])), standing_policy: Type.Optional(Type.Boolean({ description: "Include bounded energy exit, boundary replacement and local coverage repair in this plan's approval. Not an approval itself." })) }, { additionalProperties: false }),
  plan_tracking: Type.Object({ ...snapshot, algorithm_id: algorithm("distance_band"), contact_id: Type.String({ minLength: 1 }), members: Type.Optional(Type.Array(Type.String({ minLength: 1 }), { minItems: 2, maxItems: 3, uniqueItems: true })) }, { additionalProperties: false }),
  evaluate_plan: Type.Object({ result_id: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
  submit_mission_plan: Type.Object({ episode_id: Type.String({ minLength: 1 }), result_id: Type.String({ minLength: 1 }), command_id: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
  get_action_status: Type.Object({ action_id: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
};

const descriptions: Record<string, string> = {
  get_mission_state: "Read authoritative observed mission snapshot, current episode, revision, UUV poses, contacts and permissions. No target truth.",
  get_observations: "Read already-generated active detections or passive bearings. Paginate with cursor and limit; reading never generates measurements. Passive records contain no target position or range.",
  partition_search_area: "Calculate connected one-boat-per-region search responsibilities and workload from current coverage. Omitted members uses available search boats. Candidate only; no assignment or movement.",
  compute_task_allocation: "Calculate candidate search assignments or two/three-boat tracking teams for contact_id, with feasibility, energy and coverage cost. Does not execute or produce an executable mission.",
  plan_path: "Calculate oriented Dubins-compatible path for ONE member. goal=[x_m,y_m,heading_rad]. Geometry only; use plan_search for executable looping missions.",
  plan_search: "Calculate one atomic fleet-search plan when members is omitted, or responsibilities for up to eight explicit members. Includes connected regions and feasible closed search routes. Set standing_policy=true only when requested to include bounded energy rotation and local repair authorization. Returns result_id; no execution.",
  plan_tracking: "Calculate cooperative passive-bearing tracking for confirmed contact_id. Omit members to select a feasible two/three-boat team automatically. Includes observation geometry, member-specific transit, acquisition conditions, energy and remaining search coverage repair. Accepted/transit is not effective tracking. Returns result_id; no execution.",
  evaluate_plan: "Validate candidate result_id against current geometry, members and permissions. Does not authorize or execute.",
  submit_mission_plan: "Submit result_id with current episode_id and unique command_id. Backend may return pending_approval. Only execution entry. Never retry with different IDs blindly.",
  get_action_status: "Query candidate, plan or run by action_id. Accepted is not completed; pending approval means humans must decide.",
};

export function missionExtension(call: (name: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>) {
  return (pi: ExtensionAPI) => {
    pi.on("before_provider_request", (event) => ({ ...event.payload as Record<string, unknown>, max_tokens: 4096, thinking: { type: "disabled" } }));
    for (const name of TOOL_NAMES) {
      pi.registerTool({
        name, label: name, description: descriptions[name],
        parameters: schemas[name],
        execute: async (_id, parameters, signal) => {
          const result = await call(name, parameters as Record<string, unknown>, signal);
          return { content: [{ type: "text", text: JSON.stringify(result) }], details: {} };
        },
      });
    }
  };
}
