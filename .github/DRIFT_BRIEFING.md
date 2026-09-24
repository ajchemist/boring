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
| Secret | `OMNIROUTE_PROVIDER_API_KEY` | Provider API key |
| Variable | `OMNIROUTE_PROVIDER` | OmniRoute provider ID |
| Variable | `OMNIROUTE_MODEL` | Explicit provider-prefixed model ID |

Use a provider/model available to that key, not an automatic routing combo.
The runner starts `diegosouzapw/omniroute:3.8.50` pinned by manifest digest, logs into its management API
with a random temporary password, registers the provider, and creates an
inference key restricted to that connection. Its listener binds to loopback;
its database lives in tmpfs and the container is removed after each run.
Provider credentials are sent via the management API, not printed or cached.
Only bounded commit metadata, filenames and aggregate counts reach the model.
The model has no shell, GitHub token or repository write tools.

AI setup or inference failures produce a workflow warning and an explicit
notice in the issue; deterministic Git facts are still published. Missing
credentials do not silently route to a different/free provider. Inspect the
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
