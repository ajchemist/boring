import { realpathSync, writeFileSync } from "node:fs";
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
  const attempts = [];
  let active;
  // A private sidecar outside the model's readable workspace; never log raw headers.
  const save = () => {
    if (process.env.DRIFT_PI_TRACE) {
      writeFileSync(process.env.DRIFT_PI_TRACE, JSON.stringify(attempts.slice(-32)), { mode: 0o600 });
    }
  };
  const label = (value) => typeof value === "string" ? value.slice(0, 200) : undefined;
  pi.on("before_provider_request", () => { active = undefined; });
  pi.on("after_provider_response", (event) => {
    const headers = Object.fromEntries(Object.entries(event.headers).map(([k, v]) => [k.toLowerCase(), v]));
    active = {
      turn: turns, http_status: event.status,
      provider: label(headers["x-omniroute-provider"]),
      model: label(headers["x-omniroute-model"]),
      fallback_attempts: label(headers["x-omniroute-fallback-attempts"]),
    };
    attempts.push(active);
    save();
  });
  pi.on("message_end", (event) => {
    if (event.message.role !== "assistant") return;
    if (!active) {
      active = { turn: turns };
      attempts.push(active);
    }
    active.stop_reason = label(event.message.stopReason);
    active.response_model = label(event.message.responseModel);
    save();
    active = undefined;
  });
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
