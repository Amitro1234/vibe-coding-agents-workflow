#!/usr/bin/env python3
"""
Generic Code Review agent — uses Azure OpenAI to review a PR diff and posts a
single PR review comment via the GitHub CLI.

Same engine/pattern as the CI failure analyzer (OpenAI v1-compatible client).
This is a generic, stack-agnostic reviewer suitable for any repository; tune the
SYSTEM prompt per-repo if you want project-specific architecture facts.

Environment variables (set by github-cr-agent.yml):
  AZURE_OPENAI_ENDPOINT  — base_url, e.g. https://keshet-foundry.openai.azure.com/openai/v1
  AZURE_OPENAI_KEY       — API key
  AZURE_OPENAI_DEPLOYMENT— deployment/model name, e.g. gpt-4o-1
  BASE_REF               — base branch to diff against (default: main)
  PR_NUMBER              — pull request number to post the review on
"""

import os
import subprocess
import sys


def run(cmd: list, check: bool = True) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if check and result.returncode != 0:
        print(f"Command failed: {' '.join(cmd)}\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def get_diff() -> tuple:
    base = os.environ.get("BASE_REF", "main")
    files = run(["git", "diff", "--name-only", f"origin/{base}...HEAD"])
    diff = run(["git", "diff", f"origin/{base}...HEAD"])
    # Truncate to ~60k chars to stay within context limits
    if len(diff) > 60_000:
        diff = diff[:60_000] + "\n\n[... diff truncated — showing first 60k chars only ...]"
    return files, diff


def call_azure_openai(files: str, diff: str) -> str:
    from openai import OpenAI

    client = OpenAI(
        base_url=os.environ["AZURE_OPENAI_ENDPOINT"],
        api_key=os.environ["AZURE_OPENAI_KEY"],
    )

    system = (
        "You are a careful senior code reviewer. Review only the changed lines in the "
        "diff. Report real correctness bugs, security or secret-handling risks, missing "
        "tests for risky behavior, backwards-compatibility breaks, and clear operational "
        "risks. Do NOT report pure formatting/whitespace preferences, broad refactors "
        "unrelated to the PR, or speculation without evidence in the diff. If there are "
        "no real issues, approve."
    )

    user = f"""Review this pull request. Focus on actual issues, not checklist pattern-matching.

## Changed files
{files}

## Diff
```
{diff}
```

## Output format (write in English)
1. **Overall verdict**: ✅ Approved / 🟠 Needs minor changes / 🔴 Needs major changes
2. **Summary** (2–3 sentences)
3. **Issues found** — for each: severity (🔴 Critical / 🟠 Important / 🟡 Suggestion), file+line, the problem, and a concrete fix
4. **What looks good**
5. Footer: *— CR Agent (Azure OpenAI)*

Review only changed lines. If there are no real issues, output ✅ Approved with a one-line summary."""

    response = client.chat.completions.create(
        model=os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-1"),
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        max_tokens=4096,
        temperature=0.2,
    )
    return response.choices[0].message.content


def post_review(body: str) -> None:
    pr_number = os.environ.get("PR_NUMBER", "")
    if not pr_number:
        print("No PR_NUMBER — cannot post review", file=sys.stderr)
        sys.exit(1)
    run(["gh", "pr", "review", pr_number, "--comment", "--body", body])
    print(f"✅ Review posted on PR #{pr_number}")


def main() -> None:
    files, diff = get_diff()
    if not diff:
        print("No diff detected — skipping review.")
        return

    print(f"Reviewing {len(diff)} chars of diff across:\n{files}\n")
    review = call_azure_openai(files, diff)
    print("Review generated. Posting to PR...")
    post_review(review)


if __name__ == "__main__":
    main()
