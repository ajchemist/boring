import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, readFileSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import guard, { insideWorkspace } from "./drift_pi_guard.mjs";

const roots = [];
afterEach(() => roots.splice(0).forEach((root) => rmSync(root, { recursive: true, force: true })));

test("inspection cannot escape to credentials through paths or symlinks", () => {
  const root = mkdtempSync(join(tmpdir(), "pi-guard-test-"));
  roots.push(root);
  const workspace = join(root, "workspace");
  mkdirSync(workspace);
  writeFileSync(join(workspace, "facts.json"), "{}");
  writeFileSync(join(root, "secret"), "hidden");
  symlinkSync(join(root, "secret"), join(workspace, "link"));
  expect(insideWorkspace(workspace, "facts.json")).toBe(true);
  expect(insideWorkspace(workspace, ".")).toBe(true);
  expect(insideWorkspace(workspace, "../secret")).toBe(false);
  expect(insideWorkspace(workspace, join(root, "secret"))).toBe(false);
  expect(insideWorkspace(workspace, "link")).toBe(false);
});

test("agent is read-only and stops after eight model turns", () => {
  const handlers = {};
  guard({ on: (event, handler) => { handlers[event] = handler; } });
  let aborted = 0;
  const ctx = { cwd: tmpdir(), abort: () => { aborted++; } };
  expect(handlers.tool_call({ toolName: "bash", input: {} }, ctx).block).toBe(true);
  expect(handlers.tool_call({ toolName: "write", input: {} }, ctx).block).toBe(true);
  for (let turn = 0; turn < 8; turn++) handlers.turn_start({}, ctx);
  expect(aborted).toBe(0);
  handlers.turn_start({}, ctx);
  expect(aborted).toBe(1);
});

test("routing records failures without reusing an earlier response or retaining private headers", () => {
  const root = mkdtempSync(join(tmpdir(), "pi-routing-test-"));
  roots.push(root);
  const previous = process.env.DRIFT_PI_TRACE;
  process.env.DRIFT_PI_TRACE = join(root, "routing.json");
  try {
    const handlers = {};
    guard({ on: (event, handler) => { handlers[event] = handler; } });
    handlers.turn_start({}, { abort() {} });
    handlers.before_provider_request();
    handlers.after_provider_response({ status: 200, headers: {
      "X-OmniRoute-Provider": "provider-one", "x-omniroute-model": "model-one",
      authorization: "secret-key", "x-omniroute-connection": "private-id",
    } });
    handlers.message_end({ message: { role: "assistant", stopReason: "toolUse" } });
    handlers.before_provider_request();
    handlers.message_end({ message: { role: "assistant", stopReason: "error", errorMessage: "secret-error" } });
    const raw = readFileSync(process.env.DRIFT_PI_TRACE, "utf8");
    const attempts = JSON.parse(raw);
    expect(attempts[0].provider).toBe("provider-one");
    expect(attempts[1]).toEqual({ turn: 1, stop_reason: "error" });
    expect(raw).not.toContain("secret");
    expect(raw).not.toContain("private-id");
  } finally {
    if (previous === undefined) delete process.env.DRIFT_PI_TRACE;
    else process.env.DRIFT_PI_TRACE = previous;
  }
});
