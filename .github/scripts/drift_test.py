import json
import os
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import drift_briefing as drift
import drift_omniroute as omni


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


class OmniRouteTests(unittest.TestCase):
    def setUp(self):
        # Mock failure annotations must not appear as real workflow warnings.
        output = patch("builtins.print")
        output.start()
        self.addCleanup(output.stop)

    def test_missing_credentials_produce_explicit_fallback(self):
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "facts.json").write_text("{}")
            with patch.dict(os.environ, {}, clear=True), patch("sys.argv", ["test", folder]):
                omni.main()
            self.assertIn("생략", json.loads(Path(folder, "ai.json").read_text())["status"])

    def test_model_failure_still_leaves_publishable_status(self):
        env = dict(OMNIROUTE_PROVIDER="test", OMNIROUTE_MODEL="test/model",
                   OMNIROUTE_PROVIDER_API_KEY="secret-value")
        with tempfile.TemporaryDirectory() as folder:
            Path(folder, "facts.json").write_text("{}")
            with patch.dict(os.environ, env), patch("sys.argv", ["test", folder]), \
                    patch.object(omni, "summarize", side_effect=ValueError("secret-value")), \
                    patch.object(omni.subprocess, "run"):
                omni.main()
            result = Path(folder, "ai.json").read_text()
            self.assertIn("실패", result)
            self.assertNotIn("secret-value", result)


if __name__ == "__main__":
    unittest.main()
