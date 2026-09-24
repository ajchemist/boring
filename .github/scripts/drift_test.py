import json
import os
import shutil
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import drift_briefing as drift
import drift_pi as agent


class GitMeasurementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.previous = os.getcwd()
        os.chdir(self.temp.name)
        drift.git("init", "-b", "main")
        drift.git("config", "user.name", "Test")
        drift.git("config", "user.email", "test@example.invalid")
        self.base = self.commit("shared.txt", "base", "base")
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        os.chdir(self.previous)
        self.temp.cleanup()

    def commit(self, filename, content, subject):
        Path(filename).write_text(content)
        drift.git("add", "--", filename)
        drift.git("commit", "-m", subject)
        return drift.git("rev-parse", "HEAD")

    def test_identical_and_fast_forward(self):
        data = drift.measure(self.base, self.base, self.now)
        self.assertEqual((data["ahead"], data["behind"]), (0, 0))
        upstream = self.commit("upstream.txt", "new", "upstream")
        data = drift.measure(upstream, self.base, self.now)
        self.assertEqual((data["ahead"], data["behind"]), (0, 1))
        self.assertEqual(data["overlap"], [])

    def test_diverged_history_and_unusual_filename(self):
        upstream = self.commit("shared.txt", "upstream", "upstream change")
        upstream = self.commit("odd\nname.txt", "upstream", "another upstream change")
        drift.git("checkout", "-b", "fork", self.base)
        self.commit("shared.txt", "fork", "fork change")
        fork = self.commit("odd\nname.txt", "fork", "fork addition")
        data = drift.measure(upstream, fork, self.now)
        self.assertEqual((data["ahead"], data["behind"]), (2, 2))
        self.assertEqual(data["overlap"], ["odd\nname.txt", "shared.txt"])
        self.assertEqual(data["upstream_commits"][0]["sha"], upstream)
        self.assertEqual(data["fork_commits"][0]["sha"], fork)

    def test_seoul_date_and_old_commits(self):
        data = drift.measure(self.base, self.base,
                             datetime(2099, 1, 1, 16, tzinfo=timezone.utc))
        self.assertEqual(data["date"], "2099-01-02")
        self.assertEqual(data["upstream_recent_count"], 0)

    def test_unrelated_histories_fail_instead_of_reporting_zero(self):
        drift.git("checkout", "--orphan", "unrelated")
        fork = self.commit("new.txt", "new", "unrelated")
        with self.assertRaises(subprocess.CalledProcessError):
            drift.measure(self.base, fork, self.now)

    def test_commit_list_truncation_does_not_change_count(self):
        with patch.object(drift, "LIMIT", 1):
            self.commit("a", "1", "first")
            upstream = self.commit("a", "2", "second")
            data = drift.measure(upstream, self.base, self.now)
        self.assertEqual(data["behind"], 2)
        self.assertEqual(len(data["upstream_commits"]), 1)
        self.assertIn("전체 2개", drift.commit_list(data["upstream_commits"], drift.UPSTREAM, 2))

    def test_render_keeps_marker_and_bounds_github_body(self):
        data = drift.measure(self.base, self.base, self.now)
        data.update(upstream_branch="main", fork_branch="main")
        for field in ("upstream_commits", "fork_commits", "upstream_recent", "fork_recent"):
            data[field] = [{"sha": self.base, "subject": "&가" * 200}] * 30
        data["overlap"] = ["&나" * 200] * 60
        body = drift.render(data, {"summary": "@everyone <!-- forged -->"})
        self.assertLessEqual(len(body.encode()), 60000)
        self.assertTrue(body.startswith("<!-- boring-upstream-drift:"))
        self.assertNotIn("@everyone", body)
        self.assertNotIn("<!-- forged", body)

    def test_pi_snapshots_match_measured_sha_and_exclude_links(self):
        Path("outside-link").symlink_to("/etc/passwd")
        drift.git("add", "outside-link")
        fork = self.commit("shared.txt", "fork version", "fork update")
        facts = drift.measure(self.base, fork, self.now)
        Path("shared.txt").write_text("uncommitted content must not reach agent")
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            agent.prepare_workspace(Path.cwd(), facts, root)
            self.assertEqual((root / "fork/shared.txt").read_text(), "fork version")
            self.assertEqual((root / "upstream/shared.txt").read_text(), "base")
            self.assertFalse((root / "fork/outside-link").exists())
            self.assertIn("outside-link", json.loads((root / "snapshot-notes.json").read_text())["fork"])
            self.assertIn("+fork version", (root / "fork.patch").read_text())
            self.assertEqual((root / "upstream.patch").read_text(), "No changes.\n")


class PublishingTests(unittest.TestCase):
    def test_create_when_absent(self):
        with patch.object(drift, "api", side_effect=[[], {"html_url": "created"}]) as api:
            self.assertEqual(drift.publish({"date": "2026-09-24"}, "body"), "created")
        self.assertEqual(api.call_args.args[1], "POST")
        self.assertIn("state=all", api.call_args_list[0].args[0])

    def test_closed_issue_on_second_page_is_updated(self):
        existing = {"number": 42, "state": "closed",
                    "body": "<!-- boring-upstream-drift:2026-09-24 -->\nold"}
        with patch.object(drift, "api", side_effect=[
            [{"body": "unrelated"}] * 100, [existing], {"html_url": "updated"}
        ]) as api:
            self.assertEqual(drift.publish({"date": "2026-09-24"}, "new"), "updated")
        self.assertEqual(api.call_args.args[:2], (f"repos/{drift.FORK}/issues/42", "PATCH"))
        self.assertNotIn("state", api.call_args.args[2])

    def test_pull_request_marker_does_not_match(self):
        pr = {"body": "<!-- boring-upstream-drift:2026-09-24 -->", "pull_request": {}}
        with patch.object(drift, "api", side_effect=[[pr], {"html_url": "created"}]):
            self.assertEqual(drift.publish({"date": "2026-09-24"}, "body"), "created")

    def test_untrusted_titles_are_escaped(self):
        safe = drift.safe("@everyone <script> [click](bad)\nnext")
        self.assertNotIn("@everyone", safe)
        self.assertNotIn("<script>", safe)
        self.assertNotIn("\n", safe)
        self.assertIn("\\[click\\]", safe)


class PiTests(unittest.TestCase):
    def setUp(self):
        # Mock failure annotations must not appear as real workflow warnings.
        output = patch("builtins.print")
        output.start()
        self.addCleanup(output.stop)

    def test_missing_credentials_produce_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "facts.json").write_text("{}")
            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["test", folder]):
                agent.main()
            self.assertIn("생략", json.loads(Path(folder, "ai.json").read_text())["status"])

    def test_model_failure_still_leaves_publishable_status(self):
        env = dict(OMNIROUTE_BASE_URL="https://gateway.example.invalid/v1",
                   OMNIROUTE_API_KEY="secret-value")
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "facts.json").write_text("{}")
            with patch.dict(os.environ, env), patch("sys.argv", ["test", folder]), \
                    patch.object(agent, "summarize", side_effect=ValueError("secret-value")):
                agent.main()
            result = Path(folder, "ai.json").read_text()
            self.assertIn("실패", result)
            self.assertNotIn("secret-value", result)

    def test_endpoint_normalization(self):
        for base in ("https://gateway.invalid", "https://gateway.invalid/",
                     "https://gateway.invalid/v1", "https://gateway.invalid/v1/"):
            self.assertEqual(agent.api_base(base), "https://gateway.invalid/v1")
        self.assertEqual(agent.api_base("https://gateway.invalid/proxy/v1/"),
                         "https://gateway.invalid/proxy/v1")
        for base in ("http://gateway.invalid", "https://user:pass@gateway.invalid",
                     "https://gateway.invalid?key=secret"):
            with self.assertRaises(ValueError):
                agent.api_base(base)

    def test_config_uses_key_from_environment(self):
        config = agent.provider_config("https://gateway.invalid", "daily-briefing")
        provider = config["providers"]["omniroute"]
        self.assertEqual(provider["apiKey"], "${OMNIROUTE_API_KEY}")
        self.assertEqual(provider["baseUrl"], "https://gateway.invalid/v1")
        self.assertEqual(provider["models"][0]["id"], "daily-briefing")

    def event_stream(self, stop="stop", settled=True, tools=True):
        events = []
        if tools:
            for name in ("facts.json", "upstream.patch", "fork.patch"):
                events += [
                    {"type": "tool_execution_start", "toolCallId": name, "toolName": "read", "args": {"path": name}},
                    {"type": "tool_execution_end", "toolCallId": name, "toolName": "read", "isError": False},
                ]
        events += [{"type": "message_end", "message": {
            "role": "assistant", "stopReason": stop, "content": [
                {"type": "thinking", "thinking": "private intermediate thought"},
                {"type": "text", "text": "브리핑\u2028완료"},
            ],
        }}]
        if settled:
            events += [{"type": "agent_settled"}]
        return "\n".join(json.dumps(e, ensure_ascii=False) for e in events)

    def test_only_completed_answer_and_verified_tool_use_are_published(self):
        result = agent.parse_events(self.event_stream())
        self.assertEqual(result["summary"], "브리핑\u2028완료")
        self.assertEqual(result["tool_calls"], {"read": 3})
        for options in ({"stop": "error"}, {"stop": "length"}, {"settled": False}, {"tools": False}):
            with self.assertRaises(ValueError):
                agent.parse_events(self.event_stream(**options))

    def test_failed_last_turn_cannot_publish_earlier_partial_answer(self):
        failed = {"type": "message_end", "message": {"role": "assistant", "stopReason": "error", "content": []}}
        with self.assertRaises(ValueError):
            agent.parse_events(self.event_stream() + "\n" + json.dumps(failed))

    def test_combo_default_and_credentials_are_redacted(self):
        env = dict(OMNIROUTE_BASE_URL="https://gateway.invalid/v1", OMNIROUTE_API_KEY="secret-value")
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "facts.json").write_text("{}")
            with patch.dict(os.environ, env, clear=True), patch("sys.argv", ["test", folder]), \
                    patch.object(agent, "summarize", return_value={
                        "summary": "secret-value https://gateway.invalid/v1 gateway.invalid",
                        "model": "auto", "status": "ok", "tool_calls": {"read": 3},
                    }) as summarize:
                agent.main()
            self.assertEqual(summarize.call_args.args[-1], "auto")
            result = json.loads(Path(folder, "ai.json").read_text())
            self.assertEqual(result["status"], "ok")
            self.assertNotIn("secret-value", result["summary"])
            self.assertNotIn("gateway.invalid", result["summary"])


@unittest.skipUnless(shutil.which("pi") and shutil.which("bun"), "Install the pinned Pi CLI with Bun")
class PiRuntimeTests(unittest.TestCase):
    def test_actual_pi_streams_tool_calls_and_returns_final_answer(self):
        requests = []

        class Gateway(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append((dict(self.headers), body))
                done = any(message["role"] == "tool" for message in body["messages"])
                if done:
                    delta = {"role": "assistant", "content": "검토 완료"}
                    finish = "stop"
                else:
                    delta = {"role": "assistant", "tool_calls": [
                        {"index": i, "id": f"call_{i}", "type": "function", "function": {
                            "name": "read", "arguments": json.dumps({"path": name}),
                        }} for i, name in enumerate(("facts.json", "upstream.patch", "fork.patch"))
                    ]}
                    finish = "tool_calls"
                chunks = [{"id": "test", "object": "chat.completion.chunk", "model": "auto",
                           "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
                          {"id": "test", "object": "chat.completion.chunk", "model": "auto",
                           "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]}]
                data = ("".join("data: " + json.dumps(c) + "\n\n" for c in chunks) + "data: [DONE]\n\n").encode()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            def evidence(repo, facts, workspace):
                for name in ("facts.json", "upstream.patch", "fork.patch"):
                    (workspace / name).write_text("test evidence")
            with patch.object(agent, "prepare_workspace", side_effect=evidence), \
                    patch.object(agent, "api_base", side_effect=lambda base: base):
                result = agent.summarize({}, f"http://127.0.0.1:{server.server_port}/v1", "test-key", "auto")
            self.assertEqual(result["summary"], "검토 완료")
            self.assertEqual(result["tool_calls"], {"read": 3})
            self.assertEqual(len(requests), 2)
            headers = {key.lower(): value for key, value in requests[0][0].items()}
            self.assertEqual(headers["authorization"], "Bearer test-key")
            self.assertEqual(headers["user-agent"], "boring-drift-briefing/1.0")
            self.assertTrue(requests[0][1]["stream"])
            self.assertTrue(all(tool["type"] == "function" for tool in requests[0][1]["tools"]))
        finally:
            server.shutdown()
            server.server_close()
            thread.join()


if __name__ == "__main__":
    unittest.main()
