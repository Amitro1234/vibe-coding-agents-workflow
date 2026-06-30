"""
CI Failure Analysis — severity classifier.

Reads failure context from environment variables, calls Azure OpenAI
(via the OpenAI v1-compatible client — same convention as the PromoAgent CR agent),
and writes two files consumed by the GitHub Actions workflow:
  /tmp/ci_analysis_severity.txt    — one of: blocker | minor | infra
  /tmp/ci_analysis_explanation.txt — 2-3 sentence Hebrew explanation

Environment variables (all set by ci-failure-analysis.yml):
  AZURE_OPENAI_ENDPOINT  — base_url, e.g. https://keshet-foundry.openai.azure.com/openai/v1
  AZURE_OPENAI_KEY       — API key
  AZURE_OPENAI_DEPLOYMENT— deployment/model name, e.g. gpt-4o-1
  FAILURE_OWNER, FAILURE_REPO, FAILURE_RUN_ID, FAILURE_RUN_URL
  FAILURE_WORKFLOW, FAILURE_STEP, FAILURE_ERROR, FAILURE_BRANCH
  FAILURE_CHANGED_FILES  — newline-separated list of files changed in the PR (optional)
"""

import json
import os
import sys

from openai import OpenAI


def main() -> None:
    endpoint = os.environ["AZURE_OPENAI_ENDPOINT"]
    api_key = os.environ["AZURE_OPENAI_KEY"]
    deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o-1")

    context = {
        "owner": os.environ.get("FAILURE_OWNER", ""),
        "repo": os.environ.get("FAILURE_REPO", ""),
        "run_id": os.environ.get("FAILURE_RUN_ID", ""),
        "run_url": os.environ.get("FAILURE_RUN_URL", ""),
        "workflow_name": os.environ.get("FAILURE_WORKFLOW", ""),
        "failed_step": os.environ.get("FAILURE_STEP", ""),
        "error_message": os.environ.get("FAILURE_ERROR", "").strip()[:1500],
        "branch": os.environ.get("FAILURE_BRANCH", ""),
        "changed_files": os.environ.get("FAILURE_CHANGED_FILES", "").strip()[:1500],
    }

    prompt = f"""You are a senior SRE analyzing a GitHub Actions CI failure.

Your job is to classify the failure into exactly ONE of these three severity levels:

- **blocker**: A code bug, test failure, compilation error, or type error introduced
  by recent code changes that MUST be fixed before the branch can merge.
  The fix requires a code change → open a PR.

- **minor**: A non-critical issue such as a lint warning, deprecated API usage,
  a known flaky test, or a formatting check. It does not block the team but should
  be tracked → open an issue.

- **infra**: An infrastructure, credentials, secrets, environment, or external
  service problem that is NOT caused by application code changes.
  Examples: expired token, missing secret, quota limit, network timeout, Docker Hub
  rate limit, third-party API down.
  No code fix is needed → inform the team.

Failure context:
- Repository : {context['owner']}/{context['repo']}
- Workflow   : {context['workflow_name']}
- Branch     : {context['branch']}
- Failed step: {context['failed_step'] or '(unknown)'}
- Changed files in the PR:
```
{context['changed_files'] or '(no PR diff available — likely a push/post-merge failure)'}
```
- Error lines:
```
{context['error_message'] or '(no error lines extracted — base your assessment on the step name and workflow context)'}
```

Correlate the error lines with the changed files when deciding severity: a failure in
code that was just changed leans `blocker`; a failure unrelated to the diff (or with no
diff) leans `infra` or `minor`.

Respond with valid JSON only, no markdown fences:
{{
  "severity": "blocker" | "minor" | "infra",
  "explanation": "2-3 sentence explanation written in Hebrew. State the likely root cause, why you chose this severity, and what action you recommend."
}}"""

    client = OpenAI(base_url=endpoint, api_key=api_key)

    response = client.chat.completions.create(
        model=deployment,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0,
        max_tokens=400,
    )

    raw = response.choices[0].message.content or "{}"
    result = json.loads(raw)

    severity = result.get("severity", "infra").strip().lower()
    if severity not in ("blocker", "minor", "infra"):
        print(f"WARNING: unexpected severity '{severity}', defaulting to 'infra'", file=sys.stderr)
        severity = "infra"

    explanation = result.get("explanation", "לא ניתן לקבוע את חומרת הכשל.").strip()

    with open("/tmp/ci_analysis_severity.txt", "w", encoding="utf-8") as f:
        f.write(severity)
    with open("/tmp/ci_analysis_explanation.txt", "w", encoding="utf-8") as f:
        f.write(explanation)

    print(f"Severity   : {severity}")
    print(f"Explanation: {explanation}")


if __name__ == "__main__":
    main()
