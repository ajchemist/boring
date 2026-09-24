# Daily upstream drift briefing

`daily_upstream_briefing.yml` compares the default branches of `alebeck/boring`
and `ajchemist/boring` daily at **09:17 Asia/Seoul** (`17 0 * * *` UTC), or on
manual dispatch. GitHub may delay scheduled runs. Scheduling requires the
workflow on the default branch and enabled Actions; GitHub may disable public
repository schedules after 60 days without repository activity.

Each Seoul date gets a new Korean issue, even when there are no changes. Reruns
update that day's issue, including a closed issue, without reopening it.
Issues must be enabled in the fork. The workflow uses `GITHUB_TOKEN` with
`contents: read` and `issues: write`; no PAT is needed.

## Pi coding agent and OmniRoute

Configure these under Settings → Secrets and variables → Actions:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `OMNIROUTE_BASE_URL` | HTTPS gateway base URL, optionally ending in `/v1` |
| Secret | `OMNIROUTE_API_KEY` | Gateway inference API key |
| Variable | `OMNIROUTE_MODEL` | Optional model/combo ID; defaults to `auto` |

The runner installs **Pi coding agent 0.87.1** using Bun 1.4.2. The Python wrapper
prepares evidence, starts `pi --print --mode json`, and extracts its completed
answer. Python does not send LLM API requests. Pi owns the model/tool loop,
streaming and retry handling, and connects to the existing OmniRoute gateway
through a temporary custom `models.json` provider (`openai-completions`). The
gateway key is resolved from the environment, not embedded in that file.

Providers, their multiple API keys, routing policies, fallbacks and usage state
remain managed on the OmniRoute server. `auto` delegates model selection there;
`OMNIROUTE_MODEL` can select a named combo with streaming/tool-call support.
The gateway must be reachable from GitHub-hosted runners. Pi sends the
`boring-drift-briefing/1.0` User-Agent. Endpoint/key values and raw agent events
are not printed or included in the issue.

Pi receives a disposable workspace containing `facts.json`, `upstream.patch`,
`fork.patch`, and both source snapshots at the measured commit SHAs. It must use
tools to read the facts and both patches, then may inspect relevant source.
Only `read`, `grep`, `find` and `ls` are available; a repository-owned extension
keeps tool paths inside the workspace and limits the run to eight model turns.
No shell, write tools, GitHub token, personal Pi configuration, discovered
extensions, skills or repository context instructions are loaded. Pi's config
lives outside the tool-readable workspace. Sessions are ephemeral; temporary
files are removed after the run. The process group has a seven-minute timeout;
Pi retries transient inference failures once after a 60-second backoff.

Only a settled, successful final assistant message is eligible for publication,
and required evidence reads must have succeeded. The issue includes the Pi
version and successful tool-call counts. Thinking and intermediate messages are
not published. Issue creation remains a separate deterministic workflow step.

Agent setup, tool-loop or inference failures produce a workflow warning and an explicit
notice in the issue; deterministic Git facts are still published. Missing
credentials skip inference. Provider fallback follows the server's routing policy. Inspect the
issue's AI section to confirm that inference succeeded.

## Interpreting the report

- Ahead/behind counts use full Git history and SHA ancestry, not patch equivalence.
- Activity covers the last 24 hours by committer timestamp, not push time or
  changes since the previous issue. Shared commits can appear on both sides.
- File overlap compares each tip to their common ancestor. It identifies review
  candidates, not actual merge conflicts. No merge or upstream code is executed.
- Commit lists show the newest 30 per section; overlap lists show up to 60 paths.
  Counts remain complete. Each agent patch is capped at 120,000 bytes with an
  explicit truncation notice. Snapshots omit symlinks, special files, files over
  1 MiB, and files beyond a 20 MiB per-snapshot budget; omissions are listed in
  `snapshot-notes.json`. Source and patch contents inspected by Pi reach the model.
- Collection/API failures fail the job rather than publish a misleading zero-drift report.
- Repository contents are never modified by the daily workflow.

## Local verification

```sh
python3 -m unittest discover -s .github/scripts -p 'drift_test.py' -v
bun test ./.github/scripts/drift_pi_guard.test.mjs
actionlint .github/workflows/daily_upstream_briefing.yml
bun add --global @earendil-works/pi-coding-agent@0.87.1
python3 .github/scripts/drift_briefing.py collect --output /tmp/boring-drift
python3 .github/scripts/drift_pi.py /tmp/boring-drift
python3 .github/scripts/drift_briefing.py render --output /tmp/boring-drift
```

`collect` requires authenticated `gh` and fetches the two public default branches.
`render` writes `briefing.md` without posting. `publish` creates/updates the issue.
