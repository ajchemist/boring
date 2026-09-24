import { realpathSync } from "node:fs";
import { isAbsolute, relative, resolve, sep } from "node:path";

export function insideWorkspace(root, input) {
  try {
    const path = realpathSync(resolve(root, input || "."));
    const rel = relative(realpathSync(root), path);
    return rel !== ".." && !rel.startsWith(`..${sep}`) && !isAbsolute(rel);
  } catch {
    return false;
  }
}

export default function (pi) {
  let turns = 0;
  pi.on("turn_start", (_event, ctx) => {
    if (++turns > 8) ctx.abort();
  });
  pi.on("tool_call", (event, ctx) => {
    if (!["read", "grep", "find", "ls"].includes(event.toolName)) {
      return { block: true, reason: "Only read-only inspection tools are allowed." };
    }
    if (!insideWorkspace(ctx.cwd, event.input.path)) {
      return { block: true, reason: "Inspect files inside the briefing workspace only." };
    }
  });
}
