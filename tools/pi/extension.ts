import { Type } from "typebox";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { TOOL_NAMES } from "./config.ts";

const descriptions: Record<string, string> = {
  get_mission_state: "Read authoritative observed mission snapshot, current episode, revision, UUV poses, contacts and permissions. No target truth.",
  get_observations: "Read recent measured target observations. after_s and limit optional.",
  compute_task_allocation: "Calculate candidate teams for all eight UUVs, max three each. Does not execute. Optional tasks: id, center meters, size, priority.",
  plan_path: "Calculate oriented Dubins-compatible path for ONE member. goal=[x_m,y_m,heading_rad]. Geometry only; use plan_search for executable looping missions.",
  plan_search: "Calculate executable search or reacquisition plan for 1-3 members. bbox=[xmin,ymin,xmax,ymax] in METERS, at least 600m wide per member. Omit bbox for full map. Returns result_id; does not execute.",
  plan_tracking: "Calculate distance-band tracking policy for confirmed contact_id and 1-3 members. Returns result_id. Does not execute.",
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
        parameters: Type.Object({
          episode_id: Type.Optional(Type.String()),
          mission_revision: Type.Optional(Type.Integer({ minimum: 0 })),
          algorithm_id: Type.Optional(Type.Union([Type.Literal("default"), Type.Literal("dubins_hybrid"), Type.Literal("strip_coverage"), Type.Literal("slot_assignment"), Type.Literal("distance_band")])),
          members: Type.Optional(Type.Array(Type.String(), { minItems: 1, maxItems: 3, uniqueItems: true })),
          bbox: Type.Optional(Type.Array(Type.Number({ minimum: 0, maximum: 4000 }), { minItems: 4, maxItems: 4 })),
          goal: Type.Optional(Type.Array(Type.Number(), { minItems: 3, maxItems: 3 })),
          contact_id: Type.Optional(Type.String()), result_id: Type.Optional(Type.String()),
          command_id: Type.Optional(Type.String()), action_id: Type.Optional(Type.String()),
          mode: Type.Optional(Type.Union([Type.Literal("search"), Type.Literal("reacquire")])),
          after_s: Type.Optional(Type.Number()), limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 100 })),
          tasks: Type.Optional(Type.Array(Type.Object({ id: Type.String(), center: Type.Array(Type.Number(), { minItems: 2, maxItems: 2 }), size: Type.Integer({ minimum: 1, maximum: 3 }), priority: Type.Number({ minimum: 1, maximum: 10 }) }), { maxItems: 8 })),
        }, { additionalProperties: false }),
        execute: async (_id, parameters, signal) => {
          const result = await call(name, parameters, signal);
          return { content: [{ type: "text", text: JSON.stringify(result) }], details: {} };
        },
      });
    }
  };
}
