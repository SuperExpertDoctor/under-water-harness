import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { backendToolAdapter, type BackendToolSpec } from "./tool-adapter.ts";

/**
 * Mission-tool extension. Tool specs are no longer defined here — the
 * backend catalog (GET /internal/agent/tools) is the single source: ten
 * builtin tools live in tools/uuv_game/agent_tools/, and any plugin file
 * that declares TOOLS joins the same catalog. Every entry goes through
 * backendToolAdapter, which translates it into a full ToolDefinition
 * (description + promptSnippet + promptGuidelines + parameters +
 * constrainedSampling + executionMode + execute).
 */
export function missionExtension(call: (name: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>, specs: BackendToolSpec[]) {
  return (pi: ExtensionAPI) => {
    pi.on("before_provider_request", (event) => ({ ...event.payload as Record<string, unknown>, max_tokens: 4096, thinking: { type: "disabled" } }));
    for (const spec of specs) {
      pi.registerTool(backendToolAdapter(spec, call));
    }
  };
}
