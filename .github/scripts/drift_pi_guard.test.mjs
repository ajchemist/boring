import { afterEach, expect, test } from "bun:test";
import { mkdtempSync, mkdirSync, rmSync, symlinkSync, writeFileSync } from "node:fs";
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
