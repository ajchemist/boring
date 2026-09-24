"""Read Git history and publish one Korean drift briefing per Seoul date."""

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote
from zoneinfo import ZoneInfo

FORK = "ajchemist/boring"
UPSTREAM = "alebeck/boring"
LIMIT = 30


def run(*args, input=None):
    return subprocess.run(args, input=input, text=True, capture_output=True,
                          check=True, timeout=120).stdout


def api(path, method="GET", body=None):
    args = ["gh", "api", path, "--method", method]
    if body is not None:
        args += ["--input", "-"]
    return json.loads(run(*args, input=json.dumps(body) if body is not None else None))


def git(*args):
    return run("git", *args).rstrip("\n")


def count(revision, *args):
    return int(git("rev-list", "--count", *args, revision, "--"))


def commits(revision, *args):
    lines = git("log", f"-{LIMIT}", "--format=%H%x09%s", *args, revision, "--")
    return [dict(zip(("sha", "subject"), line.split("\t", 1)))
            for line in lines.splitlines() if line]


def changed_files(base, tip):
    # NUL separation preserves filenames containing newlines, tabs or quotes.
    raw = git("diff", "--name-only", "--no-renames", "-z", base, tip, "--")
    return set(raw.split("\0")) - {""}


def measure(upstream, fork, now):
    bases = git("merge-base", "--all", upstream, fork).splitlines()
    if len(bases) != 1:
        raise RuntimeError("Expected one common ancestor; manual history review required")
    base = bases[0]
    since = (now - timedelta(hours=24)).isoformat()
    upstream_files = changed_files(base, upstream)
    fork_files = changed_files(base, fork)
    behind = count(f"{fork}..{upstream}")
    ahead = count(f"{upstream}..{fork}")
    return {
        "generated_at": now.isoformat(),
        "date": now.astimezone(ZoneInfo("Asia/Seoul")).date().isoformat(),
        "base": base, "upstream_sha": upstream, "fork_sha": fork,
        "ahead": ahead, "behind": behind,
        "upstream_commits": commits(f"{fork}..{upstream}"),
        "fork_commits": commits(f"{upstream}..{fork}"),
        "upstream_recent": commits(upstream, f"--since-as-filter={since}"),
        "fork_recent": commits(fork, f"--since-as-filter={since}"),
        "upstream_recent_count": count(upstream, f"--since-as-filter={since}"),
        "fork_recent_count": count(fork, f"--since-as-filter={since}"),
        "upstream_file_count": len(upstream_files),
        "fork_file_count": len(fork_files),
        "overlap_count": len(upstream_files & fork_files),
        "overlap": sorted(upstream_files & fork_files)[:60],
        "upstream_files": sorted(upstream_files)[:60],
        "fork_files": sorted(fork_files)[:60],
    }


def safe(text):
    text = " ".join(str(text).split())[:160]
    text = html.escape(text).replace("@", "&#64;")
    return re.sub(r"([\\`*_[\]{}()!|])", r"\\\1", text)


def commit_list(items, repo, total):
    lines = [f"- [`{c['sha'][:8]}`](https://github.com/{repo}/commit/{c['sha']}) "
             f"{safe(c['subject'])}" for c in items]
    if total > len(items):
        lines.append(f"- 전체 {total}개 중 최근 {len(items)}개 표시.")
    return "\n".join(lines) or "없음."


def render(data, ai):
    ahead, behind = data["ahead"], data["behind"]
    if ahead and behind:
        status = "양쪽에 고유 커밋이 있어 분기된 상태입니다. upstream 반영 여부를 검토하세요."
    elif behind:
        status = "fork가 upstream보다 뒤처져 있습니다. fast-forward 가능한 계보입니다."
    elif ahead:
        status = "upstream 커밋은 모두 포함되어 있고 fork 전용 커밋이 있습니다."
    else:
        status = "두 브랜치의 커밋이 동일합니다."
    overlap = "\n".join(f"- {safe(p)}" for p in data["overlap"]) or "없음."
    ai_text = ai.get("summary") or ai.get("status", "AI 요약을 실행하지 않았습니다.")
    # Generated prose cannot create mentions or forge our deduplication marker.
    ai_text = ai_text.replace("@", "＠").replace("<!--", "&lt;!--")[:6500]
    if ai.get("summary") and ai.get("model"):
        ai_text += f"\n\n모델: {safe(ai['model'])}"
    if ai.get("harness"):
        calls = ", ".join(f"{safe(tool)} {count}회" for tool, count in ai.get("tool_calls", {}).items())
        ai_text += f"\n\n하네스: {safe(ai['harness'])} · 도구 실행: {calls}"
    sections = [
        f"<!-- boring-upstream-drift:{data['date']} -->",
        f"# Upstream / fork 드리프트 — {data['date']} (KST)",
        f"기준 시각: {data['generated_at']} · 비교 대상: "
        f"`{UPSTREAM}:{safe(data['upstream_branch'])}` ↔ `{FORK}:{safe(data['fork_branch'])}`",
        "## 상태\n\n" + status,
        "| 항목 | 수치 |\n| --- | ---: |\n"
        f"| fork에 없는 upstream 커밋 (behind) | {behind} |\n"
        f"| upstream에 없는 fork 커밋 (ahead) | {ahead} |\n"
        f"| 최근 24시간 upstream 커밋 | {data['upstream_recent_count']} |\n"
        f"| 최근 24시간 fork 커밋 | {data['fork_recent_count']} |\n"
        f"| 공통 조상 이후 upstream 변경 파일 | {data['upstream_file_count']} |\n"
        f"| 공통 조상 이후 fork 변경 파일 | {data['fork_file_count']} |\n"
        f"| 양쪽 모두 변경한 파일 | {data['overlap_count']} |",
        "커밋 수는 SHA/계보 기준입니다. squash·cherry-pick의 패치 동등성은 판정하지 않습니다. "
        "최근 24시간은 committer 시각 기준이며, 새로 push된 시각이나 전일 보고 이후 변화와 다릅니다. "
        "공유 커밋은 양쪽 최근 활동에 모두 포함될 수 있습니다.",
        "## AI 검토 메모\n\n" + ai_text +
        "\n\n_AI 해석은 참고용입니다. 위 수치는 Git으로 직접 계산했습니다._",
        "## fork에 없는 upstream 커밋\n\n" +
        commit_list(data["upstream_commits"], UPSTREAM, behind),
        "## fork 전용 커밋\n\n" + commit_list(data["fork_commits"], FORK, ahead),
        "## 최근 24시간 upstream 활동\n\n" +
        commit_list(data["upstream_recent"], UPSTREAM, data["upstream_recent_count"]),
        "## 최근 24시간 fork 활동\n\n" +
        commit_list(data["fork_recent"], FORK, data["fork_recent_count"]),
        "## 양쪽에서 변경한 파일\n\n" + overlap +
        "\n\n공통 조상 대비 각 tip의 변경 경로 교집합입니다(최대 60개 표시). "
        "충돌 가능성을 살펴볼 후보이며 실제 merge 충돌 판정은 아닙니다.",
        "## 비교 근거\n\n"
        f"- [upstream tip](https://github.com/{UPSTREAM}/commit/{data['upstream_sha']})\n"
        f"- [fork tip](https://github.com/{FORK}/commit/{data['fork_sha']})\n"
        f"- 공통 조상: `{data['base']}`\n"
        f"- [upstream → fork 비교](https://github.com/{FORK}/compare/"
        f"{data['upstream_sha']}...{data['fork_sha']})\n"
        f"- [fork → upstream 비교](https://github.com/{UPSTREAM}/compare/"
        f"{data['fork_sha']}...{data['upstream_sha']})",
    ]
    if data.get("run_url"):
        sections.append(f"[워크플로우 실행 기록]({data['run_url']})")
    body = "\n\n".join(sections) + "\n"
    if len(body.encode("utf-8")) > 60000:
        body = body.encode("utf-8")[:59000].decode("utf-8", errors="ignore")
        body += "\n\n본문 크기 제한으로 상세 목록을 생략했습니다. 상단 집계 수치는 전체 기준입니다.\n"
    return body


def publish(data, body):
    title = f"[Upstream drift] {data['date']}"
    marker = f"<!-- boring-upstream-drift:{data['date']} -->"
    # List, not search: newly created issues are immediately visible; include closed issues.
    midnight = datetime.fromisoformat(data["date"]).replace(tzinfo=ZoneInfo("Asia/Seoul"))
    since = quote(midnight.astimezone(timezone.utc).isoformat(), safe="")
    page = 1
    while True:
        issues = api(f"repos/{FORK}/issues?state=all&since={since}&per_page=100&page={page}")
        for issue in issues:
            if "pull_request" not in issue and (issue.get("body") or "").startswith(marker):
                updated = api(f"repos/{FORK}/issues/{issue['number']}", "PATCH",
                              {"title": title, "body": body})
                return updated["html_url"]
        if len(issues) < 100:
            break
        page += 1
    return api(f"repos/{FORK}/issues", "POST", {"title": title, "body": body})["html_url"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["collect", "publish", "render"])
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    if args.command == "collect":
        branches = {}
        tips = {}
        for label, repo in (("fork", FORK), ("upstream", UPSTREAM)):
            branch = api(f"repos/{repo}")["default_branch"]
            git("fetch", "--no-tags", f"https://github.com/{repo}.git",
                f"+refs/heads/{branch}:refs/drift/{label}")
            tips[label] = git("rev-parse", f"refs/drift/{label}")
            branches[f"{label}_branch"] = branch
        data = measure(tips["upstream"], tips["fork"], datetime.now(timezone.utc))
        data.update(branches)
        if os.environ.get("GITHUB_RUN_ID"):
            data["run_url"] = f"https://github.com/{FORK}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
        (args.output / "facts.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))
        print(f"Collected: ahead={data['ahead']}, behind={data['behind']}")
        return
    data = json.loads((args.output / "facts.json").read_text())
    ai_path = args.output / "ai.json"
    ai = json.loads(ai_path.read_text()) if ai_path.exists() else {}
    body = render(data, ai)
    (args.output / "briefing.md").write_text(body)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
            summary.write(body)
    if args.command == "publish":
        print(publish(data, body))


if __name__ == "__main__":
    main()
