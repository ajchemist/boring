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

## OmniRoute configuration

Configure these under Settings → Secrets and variables → Actions:

| Type | Name | Value |
| --- | --- | --- |
| Secret | `OMNIROUTE_BASE_URL` | HTTPS gateway base URL, optionally ending in `/v1` |
| Secret | `OMNIROUTE_API_KEY` | Gateway inference API key |
| Variable | `OMNIROUTE_MODEL` | Optional model/combo ID; defaults to `auto` |

The runner calls the existing gateway's `/v1/chat/completions` endpoint with
Bearer authentication. Providers, their multiple API keys, routing policies,
fallbacks and usage state are managed on that server. The workflow does not
provision a container or change the server's configuration. `auto` delegates
model selection to OmniRoute; a configured named combo can be used instead.
The gateway must be reachable from GitHub-hosted runners and permit inference
with this key. Requests use `boring-drift-briefing/1.0` as the User-Agent and
reject redirects so the bearer key stays at the configured destination.
The endpoint and key are never printed or included in the issue.
Only bounded commit metadata, filenames and aggregate counts reach the model.
The model has no shell, GitHub token or repository write tools.

AI setup or inference failures produce a workflow warning and an explicit
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
  Counts remain complete. The AI input is capped further and uses no patch content.
- Collection/API failures fail the job rather than publish a misleading zero-drift report.
- Repository contents are never modified by the daily workflow.

## Local verification

```sh
python3 -m unittest discover -s .github/scripts -p 'drift_test.py' -v
actionlint .github/workflows/daily_upstream_briefing.yml
python3 .github/scripts/drift_briefing.py collect --output /tmp/boring-drift
python3 .github/scripts/drift_omniroute.py /tmp/boring-drift
python3 .github/scripts/drift_briefing.py render --output /tmp/boring-drift
```

`collect` requires authenticated `gh` and fetches the two public default branches.
`render` writes `briefing.md` without posting. `publish` creates/updates the issue.
