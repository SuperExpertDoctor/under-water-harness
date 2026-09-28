import { readFile, realpath } from "node:fs/promises";
import { isAbsolute, relative, resolve } from "node:path";
import { DefaultResourceLoader, type SettingsManager } from "@earendil-works/pi-coding-agent";
import { Type } from "typebox";
import { missionExtension } from "./extension.ts";

const trustedFiles = new Set(["SKILL.md", "references/search.md", "references/tracking.md", "references/energy-turnover.md", "references/approval-policy.md", "references/tool-contracts.md"]);

export async function readTrustedSkill(skillDirectory: string, requestedPath: string): Promise<string> {
  const directory = await realpath(skillDirectory);
  const requested = isAbsolute(requestedPath) ? resolve(requestedPath) : resolve(directory, requestedPath);
  if (!trustedFiles.has(relative(directory, requested))) throw new Error("untrusted_skill_path");
  const actual = await realpath(requested);
  if (actual !== requested) throw new Error("untrusted_skill_path");
  const text = await readFile(actual, "utf8");
  if (text.length > 32000) throw new Error("skill_resource_too_large");
  return text;
}

export function createMissionResources(directory: string, skillDirectory: string, settingsManager: SettingsManager, call: (name: string, params: Record<string, unknown>, signal?: AbortSignal) => Promise<unknown>): DefaultResourceLoader {
  return new DefaultResourceLoader({ cwd: directory, agentDir: directory, settingsManager,
    noExtensions: true, noSkills: true, noPromptTemplates: true, noThemes: true, noContextFiles: true,
    additionalSkillPaths: [resolve(skillDirectory, "SKILL.md")],
    systemPrompt: "You coordinate a 2D eight-UUV reconnaissance GAME. Reply in concise Chinese. Read the trusted multi-uuv-recon-tracking Skill using the restricted read tool before mission decisions. Only use registered mission tools and trusted skill reads. No real-world vehicle control. Never invent execution, observations or approvals. Every submit_mission_plan needs a nonempty PUBLIC decision_reason explaining the actual scheduling choice and involved UUVs; if the tool rejects a missing reason, correct it and retry. Public outputs only; do not reveal hidden reasoning or credentials.",
    extensionFactories: [missionExtension(call), (pi) => {
      pi.registerTool({ name: "read", label: "Read trusted mission skill", description: "Read only the trusted multi-uuv-recon-tracking SKILL.md or its five listed references. Accept an absolute skill path or a path relative to that skill. No arbitrary filesystem access.",
        parameters: Type.Object({ path: Type.String({ minLength: 1 }) }, { additionalProperties: false }),
        execute: async (_id, params, signal) => {
          signal?.throwIfAborted();
          const text = await readTrustedSkill(skillDirectory, params.path);
          signal?.throwIfAborted();
          return { content: [{ type: "text", text }], details: {} };
        },
      });
    }],
  });
}
