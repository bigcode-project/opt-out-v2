# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

GITHUB_API = "https://api.github.com"
REPO = "bigcode-project/opt-out-v2"
BOT_MARKER = "Your opt-out request has been automatically processed"
RATE_LIMIT_BUFFER = 50
PER_PAGE = 100
SKIP_TAGS = ["question"]


@dataclass
class Stats:
    processed: int = 0
    repaired: int = 0
    skipped_tag: int = 0
    labeled: int = 0
    failed: int = 0
    rate_limit_pauses: int = 0


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    records = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def gh_request(
    method: str,
    path: str,
    token: str,
    body: dict | None = None,
) -> tuple[int, dict | str, dict[str, str]]:
    url = f"{GITHUB_API}{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "opt-out-processor",
        },
    )
    if data:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            raw = resp.read().decode()
            try:
                parsed = json.loads(raw)
            except json.JSONDecodeError:
                parsed = raw
            return resp.status, parsed, headers
    except urllib.error.HTTPError as e:
        headers = {k.lower(): v for k, v in e.headers.items()}
        raw = e.read().decode()
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return e.code, parsed, headers


def check_rate_limit(headers: dict[str, str]) -> tuple[int, int]:
    remaining = int(headers.get("x-ratelimit-remaining", "9999"))
    reset = int(headers.get("x-ratelimit-reset", "0"))
    return remaining, reset


def wait_for_rate_limit(headers: dict[str, str], stats: Stats) -> None:
    remaining, reset = check_rate_limit(headers)
    if remaining < RATE_LIMIT_BUFFER:
        sleep_duration = max(reset - time.time() + 5, 1)
        print(
            f"  Rate limit low ({remaining} remaining)."
            f" Sleeping {sleep_duration:.0f}s..."
        )
        stats.rate_limit_pauses += 1
        time.sleep(sleep_duration)


def fetch_open_issues(
    token: str, stats: Stats
) -> dict[int, dict]:
    issues: dict[int, dict] = {}
    page = 1
    while True:
        status, data, headers = gh_request(
            "GET",
            f"/repos/{REPO}/issues?state=open&per_page={PER_PAGE}&page={page}",
            token,
        )
        if status != 200:
            print(f"Error fetching issues page {page}: {status} {data}")
            sys.exit(1)

        if not data:
            break

        for item in data:
            if "pull_request" in item:
                continue
            issues[item["number"]] = {
                "number": item["number"],
                "title": item["title"],
                "labels": [l["name"] for l in item.get("labels", [])],
            }

        wait_for_rate_limit(headers, stats)

        if len(data) < PER_PAGE:
            break
        page += 1

    return issues


def issue_has_bot_comment(
    issue_number: int, token: str, stats: Stats
) -> bool:
    page = 1
    while True:
        status, data, headers = gh_request(
            "GET",
            f"/repos/{REPO}/issues/{issue_number}/comments"
            f"?per_page={PER_PAGE}&page={page}",
            token,
        )
        if status != 200:
            print(
                f"  Warning: failed to fetch comments for"
                f" #{issue_number}: {status}"
            )
            return False

        for comment in data:
            if BOT_MARKER in comment.get("body", ""):
                return True

        wait_for_rate_limit(headers, stats)

        if len(data) < PER_PAGE:
            break
        page += 1

    return False


def ensure_label(label_name: str, token: str, stats: Stats) -> None:
    encoded = urllib.parse.quote(label_name, safe="")
    status, _, headers = gh_request(
        "GET", f"/repos/{REPO}/labels/{encoded}", token
    )
    wait_for_rate_limit(headers, stats)

    if status == 404:
        status, data, headers = gh_request(
            "POST",
            f"/repos/{REPO}/labels",
            token,
            {"name": label_name},
        )
        if status in (200, 201):
            print(f"  Created label '{label_name}'")
        else:
            print(
                f"  Warning: failed to create label"
                f" '{label_name}': {status} {data}"
            )
        wait_for_rate_limit(headers, stats)


def apply_label(
    issue_number: int,
    label_name: str,
    token: str,
    dry_run: bool,
    stats: Stats,
) -> None:
    if dry_run:
        print(
            f"  [DRY RUN] Would apply label '{label_name}'"
            f" to #{issue_number}"
        )
        stats.labeled += 1
        return

    status, data, headers = gh_request(
        "POST",
        f"/repos/{REPO}/issues/{issue_number}/labels",
        token,
        {"labels": [label_name]},
    )
    if status in (200, 201):
        stats.labeled += 1
    else:
        print(
            f"  Warning: failed to label #{issue_number}:"
            f" {status} {data}"
        )
    wait_for_rate_limit(headers, stats)


def close_issue(
    issue_number: int, token: str, stats: Stats
) -> bool:
    for attempt in range(3):
        status, _, headers = gh_request(
            "PATCH",
            f"/repos/{REPO}/issues/{issue_number}",
            token,
            {"state": "closed"},
        )
        wait_for_rate_limit(headers, stats)

        if status == 200:
            return True

        if attempt < 2:
            delay = 2**attempt
            print(
                f"  Close failed (attempt {attempt + 1}/3,"
                f" status {status}). Retrying in {delay}s..."
            )
            time.sleep(delay)

    return False


def build_reply(
    optout_owners: list[str], optout_repos: list[str]
) -> str:
    lines = [
        "Your opt-out request has been automatically processed and your"
        " data has been removed from all current and future versions of"
        " The Stack.",
    ]

    if optout_owners:
        lines.append("")
        lines.append("The following owners were removed:")
        for owner in optout_owners:
            lines.append(f"- `{owner}`")

    if optout_repos:
        lines.append("")
        lines.append("The following repositories were removed:")
        for repo in optout_repos:
            lines.append(f"- `{repo}`")

    lines.append("")
    lines.append(
        "If you believe there was a parsing error, or if some repos still"
        " show up in [Am I in The Stack]"
        "(https://huggingface.co/spaces/HuggingFaceCode/in-the-stack),"
        " please submit a new issue with more details."
    )

    return "\n".join(lines)


def process_label_pass(
    tags: dict[int, str],
    open_issues: dict[int, dict],
    token: str,
    dry_run: bool,
    stats: Stats,
    known_labels: set[str],
) -> None:
    print("\n=== Pass 1: Apply labels from issue_tags ===")

    applied = 0
    for issue_num in sorted(tags):
        tag = tags[issue_num]

        if issue_num not in open_issues:
            continue

        issue = open_issues[issue_num]
        if tag in issue["labels"]:
            continue

        if tag not in known_labels and not dry_run:
            ensure_label(tag, token, stats)
            known_labels.add(tag)

        print(f"  #{issue_num}: applying '{tag}' label")
        apply_label(issue_num, tag, token, dry_run, stats)
        applied += 1

    if applied == 0:
        print("  Nothing to label.")


def process_optout_pass(
    optouts: dict[int, dict],
    tags: dict[int, str],
    open_issues: dict[int, dict],
    token: str,
    dry_run: bool,
    limit: int,
    stats: Stats,
) -> None:
    print("\n=== Pass 2: Reply and close opt-out issues ===")

    actionable = sorted(
        issue_num
        for issue_num in optouts
        if issue_num in open_issues
        and tags.get(issue_num) not in SKIP_TAGS
    )

    for issue_num in optouts:
        if issue_num in open_issues and tags.get(issue_num) in SKIP_TAGS:
            stats.skipped_tag += 1

    if limit > 0:
        actionable = actionable[:limit]

    print(f"  {len(actionable)} issues to process")

    for i, issue_num in enumerate(actionable, 1):
        record = optouts[issue_num]
        owners = record.get("optout_owners", [])
        repos = record.get("optout_repos", [])

        print(f"  [{i}/{len(actionable)}] #{issue_num}:", end=" ")

        try:
            has_comment = issue_has_bot_comment(issue_num, token, stats)
        except Exception as exc:
            print(f"error checking comments: {exc}")
            stats.failed += 1
            continue

        if has_comment:
            print("already replied, closing (repair)")
            if dry_run:
                stats.repaired += 1
                continue
            if close_issue(issue_num, token, stats):
                stats.repaired += 1
            else:
                print(
                    f"  CRITICAL: #{issue_num} could not be closed"
                    " (repair)"
                )
                stats.failed += 1
            continue

        reply = build_reply(owners, repos)

        if dry_run:
            print(
                f"reply + close ({len(owners)} owners,"
                f" {len(repos)} repos)"
            )
            stats.processed += 1
            continue

        status, _, headers = gh_request(
            "POST",
            f"/repos/{REPO}/issues/{issue_num}/comments",
            token,
            {"body": reply},
        )
        wait_for_rate_limit(headers, stats)

        if status not in (200, 201):
            print(f"FAILED to comment: {status}")
            stats.failed += 1
            continue

        if close_issue(issue_num, token, stats):
            print(
                f"done ({len(owners)} owners, {len(repos)} repos)"
            )
            stats.processed += 1
        else:
            print(
                f"CRITICAL: #{issue_num} was replied to but NOT"
                " closed"
            )
            stats.failed += 1


def main():
    ap = argparse.ArgumentParser(
        description="Process opt-out issues on GitHub"
    )
    ap.add_argument("--data-dir", type=Path, default=Path("data"))
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--token", default=os.environ.get("GITHUB_TOKEN")
    )
    args = ap.parse_args()

    if not args.token:
        print("Error: GITHUB_TOKEN not set")
        sys.exit(1)

    auto = {
        r["issue"]: r
        for r in read_jsonl(args.data_dir / "auto_optouts.jsonl")
    }
    manual = {
        r["issue"]: r
        for r in read_jsonl(args.data_dir / "manual_optouts.jsonl")
    }
    tags = {
        r["issue"]: r["tag"]
        for r in read_jsonl(args.data_dir / "issue_tags.jsonl")
    }

    all_optouts = {**auto, **manual}

    print(
        f"Loaded: {len(auto)} auto + {len(manual)} manual"
        f" = {len(all_optouts)} optouts, {len(tags)} tags"
    )
    if args.dry_run:
        print("DRY RUN MODE — no mutations will be made\n")

    stats = Stats()
    print(f"Fetching open issues from {REPO}...")
    open_issues = fetch_open_issues(args.token, stats)
    print(f"  Found {len(open_issues)} open issues")

    known_labels: set[str] = set()
    for issue in open_issues.values():
        known_labels.update(issue["labels"])

    process_label_pass(
        tags, open_issues, args.token, args.dry_run, stats, known_labels
    )

    process_optout_pass(
        all_optouts,
        tags,
        open_issues,
        args.token,
        args.dry_run,
        args.limit,
        stats,
    )

    print(f"\n{'=' * 40}")
    print(f"Processed (replied + closed): {stats.processed}")
    print(f"Repaired (closed only):       {stats.repaired}")
    print(f"Skipped (skip-tag):           {stats.skipped_tag}")
    print(f"Labels applied:               {stats.labeled}")
    print(f"Failed:                       {stats.failed}")
    print(f"Rate-limit pauses:            {stats.rate_limit_pauses}")

    if stats.failed > 0:
        print(
            f"\nWARNING: {stats.failed} issue(s) failed."
            " Check logs above for CRITICAL entries."
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
