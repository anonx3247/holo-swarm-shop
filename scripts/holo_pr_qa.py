#!/usr/bin/env python3
"""Run Holo browser-agent PR QA against a Vercel preview deployment."""

from __future__ import annotations

import json
import os
import sys
import textwrap
import time
import urllib.error
import urllib.parse
import urllib.request
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from hashlib import sha1
from pathlib import Path
from typing import Any

from hai_agents import Client


COMMENT_MARKER = "<!-- holo-pr-qa -->"
TERMINAL_STATUSES = {"completed", "failed", "timed_out", "interrupted"}
SEVERITY_RANK = {"none": 0, "minor": 1, "major": 2, "critical": 3}

ANSWER_FORMAT: dict[str, Any] = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean"},
        "regression_detected": {"type": "boolean"},
        "severity": {
            "type": "string",
            "enum": ["none", "minor", "major", "critical"],
        },
        "confidence": {"type": "number"},
        "summary": {"type": "string"},
        "evidence": {"type": "array", "items": {"type": "string"}},
        "reproduction_steps": {"type": "array", "items": {"type": "string"}},
        "expected": {"type": "string"},
        "actual": {"type": "string"},
        "recommendation": {"type": "string"},
    },
    "required": [
        "success",
        "regression_detected",
        "severity",
        "confidence",
        "summary",
        "evidence",
    ],
}

CHECK_PLAN_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "holo_pr_check_plan",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "checks": {
                "type": "array",
                "minItems": 1,
                "maxItems": 5,
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "check_id": {
                            "type": "string",
                            "description": "Stable lowercase id using letters, numbers, and hyphens.",
                        },
                        "title": {"type": "string"},
                        "objective": {
                            "type": "string",
                            "description": "Concrete browser-testing task for a Holo agent.",
                        },
                        "reason": {
                            "type": "string",
                            "description": "Why the PR diff makes this check high-value.",
                        },
                        "paths": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Changed files that motivated this check.",
                        },
                    },
                    "required": ["check_id", "title", "objective", "reason", "paths"],
                },
            }
        },
        "required": ["checks"],
    },
}


@dataclass(frozen=True)
class PullRequest:
    number: int
    head_sha: str
    base_ref: str
    head_ref: str
    title: str
    body: str
    html_url: str


@dataclass
class CheckTask:
    check_id: str
    title: str
    objective: str
    reason: str
    paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AttemptProfile:
    attempt: int
    tier: str
    agent: str
    model: str | None
    max_steps: int
    max_time_s: int


@dataclass
class AgentRun:
    task_id: str
    attempt: int
    tier: str
    agent: str
    model: str | None
    status: str
    elapsed_s: float
    session_id: str | None
    agent_view_url: str | None
    answer: dict[str, Any]
    error: str | None = None


class Github:
    def __init__(self, repo: str, token: str) -> None:
        self.repo = repo
        self.token = token
        self.api = os.getenv("GITHUB_API_URL", "https://api.github.com")

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> Any:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            f"{self.api}{path}",
            data=data,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "User-Agent": "holo-pr-qa",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"GitHub API {method} {path} failed: {exc.code} {details}") from exc
        if not raw:
            return None
        return json.loads(raw)

    def get_pr(self, number: int) -> PullRequest:
        data = self.request("GET", f"/repos/{self.repo}/pulls/{number}")
        return PullRequest(
            number=number,
            head_sha=data["head"]["sha"],
            base_ref=data["base"]["ref"],
            head_ref=data["head"]["ref"],
            title=data.get("title") or "",
            body=data.get("body") or "",
            html_url=data.get("html_url") or "",
        )

    def list_pr_files(self, number: int) -> list[dict[str, Any]]:
        files: list[dict[str, Any]] = []
        page = 1
        while True:
            batch = self.request(
                "GET",
                f"/repos/{self.repo}/pulls/{number}/files?per_page=100&page={page}",
            )
            if not batch:
                return files
            files.extend(batch)
            if len(batch) < 100:
                return files
            page += 1

    def list_commit_statuses(self, sha: str) -> list[dict[str, Any]]:
        data = self.request("GET", f"/repos/{self.repo}/commits/{sha}/status")
        return data.get("statuses", [])

    def list_deployments(self, sha: str) -> list[dict[str, Any]]:
        query = urllib.parse.urlencode({"sha": sha, "per_page": 30})
        return self.request("GET", f"/repos/{self.repo}/deployments?{query}") or []

    def list_deployment_statuses(self, deployment_id: int) -> list[dict[str, Any]]:
        return (
            self.request(
                "GET",
                f"/repos/{self.repo}/deployments/{deployment_id}/statuses?per_page=20",
            )
            or []
        )

    def list_issue_comments(self, number: int) -> list[dict[str, Any]]:
        return self.request(
            "GET",
            f"/repos/{self.repo}/issues/{number}/comments?per_page=100",
        )

    def create_comment(self, number: int, body: str) -> None:
        self.request("POST", f"/repos/{self.repo}/issues/{number}/comments", body={"body": body})

    def update_comment(self, comment_id: int, body: str) -> None:
        self.request("PATCH", f"/repos/{self.repo}/issues/comments/{comment_id}", body={"body": body})

    def upsert_comment(self, number: int, body: str) -> None:
        for comment in self.list_issue_comments(number):
            if COMMENT_MARKER in (comment.get("body") or ""):
                self.update_comment(comment["id"], body)
                return
        self.create_comment(number, body)

    def try_upsert_comment(self, number: int, body: str) -> bool:
        try:
            self.upsert_comment(number, body)
            return True
        except RuntimeError as exc:
            message = (
                "Could not post/update the PR comment. The Holo QA workflow will "
                f"continue and write the same content to the GitHub step summary. Error: {exc}"
            )
            print(message, file=sys.stderr)
            append_step_summary(f"> {message}")
            return False


def env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)))
    except ValueError:
        return default


def load_event_pr_number() -> int | None:
    event_path = os.getenv("GITHUB_EVENT_PATH")
    if not event_path:
        return None
    try:
        with open(event_path, encoding="utf-8") as event_file:
            event = json.load(event_file)
    except FileNotFoundError:
        return None
    pull_request = event.get("pull_request") or {}
    return pull_request.get("number") or event.get("number")


def get_pr_number() -> int:
    raw = os.getenv("PR_NUMBER") or os.getenv("GITHUB_PR_NUMBER")
    if raw:
        return int(raw)
    event_number = load_event_pr_number()
    if event_number:
        return int(event_number)
    raise RuntimeError("Set PR_NUMBER or run from a pull request GitHub Actions event.")


def find_vercel_url_from_statuses(statuses: list[dict[str, Any]]) -> str | None:
    for status in statuses:
        context = (status.get("context") or "").lower()
        target_url = status.get("target_url") or ""
        if "vercel" in context and status.get("state") == "success" and target_url.startswith("http"):
            return target_url
    for status in statuses:
        target_url = status.get("target_url") or ""
        if "vercel" in target_url.lower() and target_url.startswith("http"):
            return target_url
    return None


def find_vercel_url_from_deployments(github: Github, pr: PullRequest) -> str | None:
    for deployment in github.list_deployments(pr.head_sha):
        environment = (deployment.get("environment") or "").lower()
        if "preview" not in environment and "vercel" not in json.dumps(deployment).lower():
            continue
        for status in github.list_deployment_statuses(deployment["id"]):
            target_url = status.get("target_url") or status.get("environment_url") or ""
            if status.get("state") == "success" and target_url.startswith("http"):
                return target_url
    return None


def discover_preview_url(github: Github, pr: PullRequest) -> str:
    explicit = os.getenv("VERCEL_PREVIEW_URL") or os.getenv("HOLO_PREVIEW_URL")
    if explicit:
        return explicit

    timeout_s = env_int("HOLO_PREVIEW_WAIT_SECONDS", 600)
    deadline = time.time() + timeout_s
    while True:
        from_status = find_vercel_url_from_statuses(github.list_commit_statuses(pr.head_sha))
        if from_status:
            return from_status

        from_deployment = find_vercel_url_from_deployments(github, pr)
        if from_deployment:
            return from_deployment

        if time.time() >= deadline:
            raise RuntimeError(
                "Timed out waiting for a successful Vercel preview deployment. "
                "Set VERCEL_PREVIEW_URL to override discovery."
            )
        time.sleep(15)


def summarize_files(files: list[dict[str, Any]], max_chars: int = 12000) -> str:
    chunks: list[str] = []
    for file in files:
        patch = file.get("patch") or "(binary or patch unavailable)"
        chunks.append(
            "\n".join(
                [
                    f"FILE: {file.get('filename')}",
                    f"STATUS: {file.get('status')} +{file.get('additions', 0)} -{file.get('deletions', 0)}",
                    patch,
                ]
            )
        )
    summary = "\n\n".join(chunks)
    if len(summary) <= max_chars:
        return summary
    return summary[:max_chars] + "\n\n[diff truncated for prompt budget]"


def openai_base_url() -> str:
    return os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def openai_request(payload: dict[str, Any]) -> dict[str, Any]:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is required for PR check planning.")

    req = urllib.request.Request(
        f"{openai_base_url()}/responses",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "holo-pr-qa",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"OpenAI Responses API failed: {exc.code} {details}") from exc


def response_text(response: dict[str, Any]) -> str:
    if isinstance(response.get("output_text"), str):
        return response["output_text"]
    for item in response.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and isinstance(content.get("text"), str):
                return content["text"]
    raise RuntimeError("OpenAI response did not include output text.")


def slugify_check_id(value: str, fallback: str) -> str:
    cleaned: list[str] = []
    last_hyphen = False
    for char in value.lower():
        if char.isalnum():
            cleaned.append(char)
            last_hyphen = False
        elif not last_hyphen:
            cleaned.append("-")
            last_hyphen = True
    slug = "".join(cleaned).strip("-")
    return slug[:64] or fallback


def parse_openai_tasks(plan: dict[str, Any], files: list[dict[str, Any]], max_checks: int) -> list[CheckTask]:
    changed_paths = {file.get("filename", "") for file in files}
    tasks: list[CheckTask] = []
    seen: set[str] = set()
    for index, item in enumerate(plan.get("checks", []), start=1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        objective = str(item.get("objective") or "").strip()
        reason = str(item.get("reason") or "").strip()
        if not title or not objective or not reason:
            continue
        fallback = f"openai-check-{index}"
        check_id = slugify_check_id(str(item.get("check_id") or title), fallback)
        if check_id in seen:
            check_id = f"{check_id}-{sha1(title.encode('utf-8')).hexdigest()[:6]}"
        paths = [str(path) for path in item.get("paths", []) if str(path) in changed_paths]
        add_task(tasks, seen, CheckTask(check_id, title, objective, reason, paths))
        if len(tasks) >= max_checks:
            break
    return tasks


def plan_tasks_with_openai(pr: PullRequest, files: list[dict[str, Any]], max_checks: int) -> tuple[list[CheckTask], str]:
    model = os.getenv("OPENAI_PLANNER_MODEL", "gpt-5.6-luna")
    diff_summary = summarize_files(files, max_chars=24000)
    prompt = textwrap.dedent(
        f"""
        Pull request #{pr.number}: {pr.title}
        Base branch: {pr.base_ref}
        Head branch: {pr.head_ref}
        PR body:
        {pr.body or "(empty)"}

        Changed files and patch excerpts:
        {diff_summary}

        Choose between 1 and {max_checks} high-value browser regression checks for Holo agents
        to run against the Vercel preview deployment. Pick fewer checks when the diff is
        narrow and more checks only when the diff creates distinct user-visible risks.
        The target app is a no-backend
        React/Vite commerce operations demo with storefront search/filtering, cart,
        checkout promo code HOLO15, local order creation, admin order board, and
        low-stock inventory.

        Prefer checks that exercise changed behavior and adjacent user-visible risk.
        Each check must be concrete enough for browser agents to execute independently.
        Avoid static code-review checks; these are live UI checks.
        """
    ).strip()

    response = openai_request(
        {
            "model": model,
            "input": [
                {
                    "role": "system",
                    "content": (
                        "You are a senior QA planner. Given a PR diff, produce browser "
                        "regression checks that independent Holo web agents can execute."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "text": {"format": CHECK_PLAN_FORMAT},
        }
    )
    tasks = parse_openai_tasks(json.loads(response_text(response)), files, max_checks)
    if not tasks:
        raise RuntimeError("OpenAI returned no usable check tasks.")
    return tasks[:max_checks], f"OpenAI Responses API ({model} via {openai_base_url()})"


def select_tasks_for_pr(pr: PullRequest, files: list[dict[str, Any]], max_checks: int) -> tuple[list[CheckTask], str]:
    try:
        return plan_tasks_with_openai(pr, files, max_checks)
    except Exception as exc:  # noqa: BLE001 - keep QA running if planning has a transient outage.
        print(f"OpenAI planner failed; using heuristic fallback: {exc}", file=sys.stderr)
        return select_tasks(files, max_checks), f"Heuristic fallback after OpenAI planner error: {exc}"


def add_task(tasks: list[CheckTask], seen: set[str], task: CheckTask) -> None:
    if task.check_id not in seen:
        seen.add(task.check_id)
        tasks.append(task)


def select_tasks(files: list[dict[str, Any]], max_checks: int) -> list[CheckTask]:
    paths = [file.get("filename", "") for file in files]
    path_blob = "\n".join(paths).lower()
    tasks: list[CheckTask] = []
    seen: set[str] = set()

    if any(part in path_blob for part in ["checkout", "cart", "product", "storefront"]):
        add_task(
            tasks,
            seen,
            CheckTask(
                "storefront-purchase",
                "Storefront purchase path",
                "Validate browse/search, product selection, cart behavior, promo/checkout steps, and confirmation path on the preview.",
                "Changed storefront, product, cart, or checkout code.",
                [path for path in paths if any(part in path.lower() for part in ["checkout", "cart", "product", "app/page"])],
            ),
        )

    if any(part in path_blob for part in ["login", "auth", "session", "admin"]):
        add_task(
            tasks,
            seen,
            CheckTask(
                "auth-admin-access",
                "Auth and admin access",
                "Validate login behavior, admin route protection, and the first admin screen after authentication.",
                "Changed auth, login, or admin surface.",
                [path for path in paths if any(part in path.lower() for part in ["login", "auth", "admin"])],
            ),
        )

    if any(part in path_blob for part in ["admin/orders", "orders", "kanban"]):
        add_task(
            tasks,
            seen,
            CheckTask(
                "admin-orders",
                "Admin orders workflow",
                "Validate the admin orders page, order list visibility, status movement or filtering, and obvious data regressions.",
                "Changed order administration code.",
                [path for path in paths if "order" in path.lower() or "admin" in path.lower()],
            ),
        )

    if any(part in path_blob for part in ["/api/", "route.ts", "prisma", "schema"]):
        add_task(
            tasks,
            seen,
            CheckTask(
                "api-data-integrity",
                "API and data integrity",
                "Exercise UI paths backed by changed APIs or schema, looking for broken loads, empty data, server errors, and persistence regressions.",
                "Changed API routes, Prisma schema, or seed data.",
                [path for path in paths if any(part in path.lower() for part in ["/api/", "route.ts", "prisma", "schema"])],
            ),
        )

    if any(part in path_blob for part in ["globals.css", "tailwind", "layout", ".tsx"]):
        add_task(
            tasks,
            seen,
            CheckTask(
                "responsive-visual",
                "Responsive visual regression",
                "Inspect the changed screens at desktop and mobile widths for layout overlap, unreadable text, missing controls, and broken navigation.",
                "Changed visual components, layout, or CSS.",
                [path for path in paths if any(part in path.lower() for part in ["globals.css", "tailwind", "layout", ".tsx"])],
            ),
        )

    fallback_tasks = [
        CheckTask(
            "preview-smoke",
            "Preview smoke test",
            "Open the Vercel preview and verify the app loads without runtime errors, blank pages, or broken top-level navigation.",
            "Always run a general smoke check.",
            paths,
        ),
        CheckTask(
            "primary-user-journey",
            "Primary user journey",
            "Follow the most important user path implied by the diff and verify it completes without a visible regression.",
            "Catches broad user-facing regressions not tied to one file group.",
            paths,
        ),
        CheckTask(
            "error-boundaries-console",
            "Runtime error scan",
            "Explore changed pages and look for visible Next.js errors, failed data loading, broken forms, or dead controls.",
            "Catches runtime regressions from any changed code.",
            paths,
        ),
        CheckTask(
            "navigation-regression",
            "Navigation regression",
            "Move between the changed pages and adjacent routes, verifying links, redirects, and back/forward behavior still work.",
            "Catches route-level regressions.",
            paths,
        ),
        CheckTask(
            "accessibility-basics",
            "Accessibility basics",
            "Check changed interactive controls for reachable labels, focusability, obvious keyboard traps, and unusable contrast.",
            "Catches severe usability regressions.",
            paths,
        ),
    ]
    for fallback in fallback_tasks:
        add_task(tasks, seen, fallback)
        if len(tasks) >= max_checks:
            break

    return tasks[:max_checks]


def configured_attempt_profiles() -> list[AttemptProfile]:
    tier_names = ["fast", "balanced", "deep"]
    default_agents = ["h/web-surfer-flash", "h/web-surfer-flash", "h/web-surfer-flash"]
    agent_tiers = [item.strip() for item in os.getenv("HOLO_AGENT_TIERS", "").split(",") if item.strip()]
    base_steps = env_int("HOLO_MAX_STEPS", 45)
    base_time_s = env_int("HOLO_MAX_TIME_SECONDS", 360)
    default_steps = [max(20, base_steps - 10), base_steps, base_steps + 15]
    default_times = [max(180, base_time_s - 60), base_time_s, base_time_s + 120]
    profiles: list[AttemptProfile] = []

    for index in range(3):
        attempt = index + 1
        agent = (
            os.getenv(f"HOLO_AGENT_TRY_{attempt}")
            or os.getenv(f"HAI_AGENT_TRY_{attempt}")
            or (agent_tiers[index] if index < len(agent_tiers) else None)
            or default_agents[index]
        )
        profiles.append(
            AttemptProfile(
                attempt=attempt,
                tier=os.getenv(f"HOLO_TIER_TRY_{attempt}", tier_names[index]),
                agent=agent,
                model=os.getenv(f"HOLO_MODEL_TRY_{attempt}") or os.getenv(f"HAI_MODEL_TRY_{attempt}"),
                max_steps=env_int(f"HOLO_MAX_STEPS_TRY_{attempt}", default_steps[index]),
                max_time_s=env_int(f"HOLO_MAX_TIME_SECONDS_TRY_{attempt}", default_times[index]),
            )
        )
    return profiles


def task_prompt(pr: PullRequest, preview_url: str, task: CheckTask, profile: AttemptProfile) -> str:
    return textwrap.dedent(
        f"""
        You are one of three independent Holo QA agents regression-testing a Vercel PR preview.

        Preview URL: {preview_url}
        Pull request: #{pr.number} {pr.title}
        Base branch: {pr.base_ref}
        Head branch: {pr.head_ref}
        Head SHA: {pr.head_sha}

        Check {task.check_id}: {task.title}
        Objective: {task.objective}
        Why this was selected: {task.reason}
        Relevant changed paths: {", ".join(task.paths[:20]) or "No specific paths"}
        Attempt: {profile.attempt} of 3
        Execution tier: {profile.tier}
        Agent: {profile.agent}
        Model override: {profile.model or "none"}
        Step budget: {profile.max_steps}
        Time budget seconds: {profile.max_time_s}

        Instructions:
        - Use the browser to test the preview like a user.
        - Prefer changed functionality and adjacent regression risk over generic browsing.
        - Record concrete evidence: URL, visible UI text, failed step, unexpected behavior, or screenshot-observable state.
        - Classify severity:
          - none: no regression found
          - minor: cosmetic issue or low-risk annoyance
          - major: primary workflow broken, data loss risk, auth bypass, persistent server error, or feature unusable
          - critical: app unavailable, security-sensitive exposure, checkout/payment-blocking regression, or destructive behavior
        - Only set regression_detected=true when you observed a real regression, not speculation.
        - Return JSON matching the requested schema.
        """
    ).strip()


def run_agent(pr: PullRequest, preview_url: str, _diff_summary: str, task: CheckTask, profile: AttemptProfile) -> AgentRun:
    poll_s = env_int("HOLO_POLL_SECONDS", 5)
    started = time.time()

    try:
        client = Client(api_key=os.getenv("HAI_API_KEY") or os.getenv("HOLO_API_KEY"))
        overrides: dict[str, Any] = {
            "agent.answer_format": ANSWER_FORMAT,
            "agent.environments[kind=web].start_url": preview_url,
        }
        if profile.model:
            overrides["agent.model"] = profile.model
        session = client.sessions.create_session(
            agent=profile.agent,
            messages=task_prompt(pr, preview_url, task, profile),
            max_steps=profile.max_steps,
            max_time_s=profile.max_time_s,
            overrides=overrides,
        )

        deadline = time.time() + profile.max_time_s + 90
        while time.time() < deadline:
            status = client.sessions.get_session_status(session.id)
            if status.status in TERMINAL_STATUSES:
                break
            time.sleep(poll_s)
        else:
            client.sessions.cancel_session(session.id)
            time.sleep(2)

        final_status = client.sessions.get_session_status(session.id)
        final_session = client.sessions.get_session(session.id)
        answer = final_session.latest_answer
        if not isinstance(answer, dict):
            answer = {
                "success": False,
                "regression_detected": False,
                "severity": "none",
                "confidence": 0,
                "summary": "Agent did not return structured JSON.",
                "evidence": [],
            }
        return AgentRun(
            task_id=task.check_id,
            attempt=profile.attempt,
            tier=profile.tier,
            agent=profile.agent,
            model=profile.model,
            status=final_status.status,
            elapsed_s=round(time.time() - started, 1),
            session_id=session.id,
            agent_view_url=final_session.agent_view_url,
            answer=answer,
        )
    except Exception as exc:  # noqa: BLE001 - preserve failures in PR comment.
        return AgentRun(
            task_id=task.check_id,
            attempt=profile.attempt,
            tier=profile.tier,
            agent=profile.agent,
            model=profile.model,
            status="runner_error",
            elapsed_s=round(time.time() - started, 1),
            session_id=None,
            agent_view_url=None,
            answer={
                "success": False,
                "regression_detected": False,
                "severity": "none",
                "confidence": 0,
                "summary": "Agent run failed before producing a result.",
                "evidence": [],
            },
            error=str(exc),
        )


def normalize_severity(answer: dict[str, Any]) -> str:
    severity = str(answer.get("severity") or "none").lower()
    return severity if severity in SEVERITY_RANK else "none"


def run_has_big_regression(run: AgentRun) -> bool:
    answer = run.answer
    if not bool(answer.get("regression_detected")):
        return False
    return SEVERITY_RANK[normalize_severity(answer)] >= SEVERITY_RANK["major"]


def should_escalate(run: AgentRun) -> bool:
    if run.error:
        return True
    if run.status != "completed":
        return True
    if not bool(run.answer.get("success")):
        return True
    return bool(run.answer.get("regression_detected"))


def summarize_task(task: CheckTask, runs: list[AgentRun]) -> dict[str, Any]:
    big_votes = sum(1 for run in runs if run_has_big_regression(run))
    regression_votes = sum(1 for run in runs if bool(run.answer.get("regression_detected")))
    max_severity = max((normalize_severity(run.answer) for run in runs), key=lambda item: SEVERITY_RANK[item])
    critical_votes = sum(
        1
        for run in runs
        if bool(run.answer.get("regression_detected")) and normalize_severity(run.answer) == "critical"
    )
    big_regression = big_votes >= 2 or critical_votes >= 1
    return {
        "task": task,
        "runs": runs,
        "regression_votes": regression_votes,
        "big_votes": big_votes,
        "max_severity": max_severity,
        "big_regression": big_regression,
    }


def run_all_checks(pr: PullRequest, preview_url: str, files: list[dict[str, Any]], tasks: list[CheckTask]) -> list[dict[str, Any]]:
    diff_summary = summarize_files(files)
    profiles = configured_attempt_profiles()
    futures = []
    max_workers = env_int("HOLO_MAX_WORKERS", len(tasks) * len(profiles))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        for task in tasks:
            for profile in profiles:
                futures.append(executor.submit(run_agent, pr, preview_url, diff_summary, task, profile))

        runs_by_task: dict[str, list[AgentRun]] = {task.check_id: [] for task in tasks}
        for future in as_completed(futures):
            run = future.result()
            runs_by_task[run.task_id].append(run)

    return [summarize_task(task, sorted(runs_by_task[task.check_id], key=lambda run: run.attempt)) for task in tasks]


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_comment(pr: PullRequest, preview_url: str, planner: str, summaries: list[dict[str, Any]]) -> str:
    big_regressions = [summary for summary in summaries if summary["big_regression"]]
    status = "fail" if big_regressions else "pass"
    lines = [
        COMMENT_MARKER,
        f"## Holo PR QA: {status.upper()}",
        "",
        f"Preview tested: {preview_url}",
        f"Check planner: {planner}",
        f"Checks selected: {len(summaries)}. Agents per check: 3.",
        "",
    ]

    if big_regressions:
        lines.append("CI is marked failed because at least one check found a consensus major/critical regression.")
    else:
        lines.append("CI is marked passed because no check found a consensus major/critical regression.")
    lines.append("")

    lines.extend(
        [
            "| Check | Result | Votes | Max severity | Summary |",
            "| --- | --- | ---: | --- | --- |",
        ]
    )
    for summary in summaries:
        result = "big regression" if summary["big_regression"] else "no blocking regression"
        lines.append(
            "| "
            + " | ".join(
                [
                    md_escape(summary["task"].title),
                    result,
                    f"{summary['big_votes']}/3 big, {summary['regression_votes']}/3 any",
                    summary["max_severity"],
                    md_escape(first_meaningful_summary(summary["runs"])),
                ]
            )
            + " |"
        )

    for summary in summaries:
        lines.extend(["", f"<details><summary>{summary['task'].title}</summary>", ""])
        lines.append(f"Reason selected: {summary['task'].reason}")
        if summary["task"].paths:
            lines.append(f"Changed paths: {', '.join(summary['task'].paths[:12])}")
        lines.append("")
        lines.extend(["| Try | Agent | Status | Severity | Regression | Evidence | Trajectory |", "| ---: | --- | --- | --- | --- | --- | --- |"])
        for run in summary["runs"]:
            evidence = run.answer.get("evidence") or []
            if isinstance(evidence, list):
                evidence_text = "; ".join(str(item) for item in evidence[:3])
            else:
                evidence_text = str(evidence)
            session = run.agent_view_url or run.session_id or ""
            session_link = f"[open]({session})" if str(session).startswith("http") else md_escape(session)
            agent_label = run.agent if not run.model else f"{run.agent} / {run.model}"
            if run.answer.get("regression_detected") and str(session).startswith("http"):
                session_link = f"**[failure trajectory]({session})**"
            lines.append(
                "| "
                + " | ".join(
                    [
                        f"{run.attempt} ({md_escape(run.tier)})",
                        md_escape(agent_label),
                        md_escape(run.status if not run.error else f"{run.status}: {run.error}"),
                        normalize_severity(run.answer),
                        "yes" if run.answer.get("regression_detected") else "no",
                        md_escape(evidence_text or run.answer.get("summary", "")),
                        session_link,
                    ]
                )
                + " |"
            )
        lines.extend(["", "</details>"])

    lines.extend(
        [
            "",
            "_Failure policy: this workflow fails only when at least two agents on the same check report a major/critical regression, or any agent reports a critical regression._",
        ]
    )
    return "\n".join(lines)


def first_meaningful_summary(runs: list[AgentRun]) -> str:
    for run in runs:
        summary = run.answer.get("summary")
        if summary:
            return str(summary)
    return "No structured summary returned."


def task_to_dict(task: CheckTask) -> dict[str, Any]:
    return {
        "check_id": task.check_id,
        "title": task.title,
        "objective": task.objective,
        "reason": task.reason,
        "paths": task.paths,
    }


def task_from_dict(data: dict[str, Any]) -> CheckTask:
    return CheckTask(
        check_id=str(data["check_id"]),
        title=str(data["title"]),
        objective=str(data["objective"]),
        reason=str(data["reason"]),
        paths=[str(path) for path in data.get("paths", [])],
    )


def profile_to_dict(profile: AttemptProfile) -> dict[str, Any]:
    return {
        "attempt": profile.attempt,
        "tier": profile.tier,
        "agent": profile.agent,
        "model": profile.model,
        "max_steps": profile.max_steps,
        "max_time_s": profile.max_time_s,
    }


def profile_from_dict(data: dict[str, Any]) -> AttemptProfile:
    return AttemptProfile(
        attempt=int(data["attempt"]),
        tier=str(data["tier"]),
        agent=str(data["agent"]),
        model=str(data["model"]) if data.get("model") else None,
        max_steps=int(data["max_steps"]),
        max_time_s=int(data["max_time_s"]),
    )


def pr_to_dict(pr: PullRequest) -> dict[str, Any]:
    return {
        "number": pr.number,
        "head_sha": pr.head_sha,
        "base_ref": pr.base_ref,
        "head_ref": pr.head_ref,
        "title": pr.title,
        "body": pr.body,
        "html_url": pr.html_url,
    }


def pr_from_dict(data: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(data["number"]),
        head_sha=str(data["head_sha"]),
        base_ref=str(data["base_ref"]),
        head_ref=str(data["head_ref"]),
        title=str(data["title"]),
        body=str(data.get("body") or ""),
        html_url=str(data.get("html_url") or ""),
    )


def run_to_dict(run: AgentRun) -> dict[str, Any]:
    return {
        "task_id": run.task_id,
        "attempt": run.attempt,
        "tier": run.tier,
        "agent": run.agent,
        "model": run.model,
        "status": run.status,
        "elapsed_s": run.elapsed_s,
        "session_id": run.session_id,
        "agent_view_url": run.agent_view_url,
        "answer": run.answer,
        "error": run.error,
    }


def run_from_dict(data: dict[str, Any]) -> AgentRun:
    return AgentRun(
        task_id=str(data["task_id"]),
        attempt=int(data["attempt"]),
        tier=str(data["tier"]),
        agent=str(data["agent"]),
        model=str(data["model"]) if data.get("model") else None,
        status=str(data["status"]),
        elapsed_s=float(data.get("elapsed_s") or 0),
        session_id=str(data["session_id"]) if data.get("session_id") else None,
        agent_view_url=str(data["agent_view_url"]) if data.get("agent_view_url") else None,
        answer=data.get("answer") if isinstance(data.get("answer"), dict) else {},
        error=str(data["error"]) if data.get("error") else None,
    )


def write_json(path: str | Path, data: dict[str, Any]) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")


def load_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_github_output(name: str, value: str) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            output.write(f"{name}={value}\n")


def github_annotation_escape(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A").replace(":", "%3A").replace(",", "%2C")


def append_step_summary(markdown: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(markdown)
            summary.write("\n")


def render_plan_comment(pr: PullRequest, preview_url: str, planner: str, tasks: list[CheckTask], profiles: list[AttemptProfile]) -> str:
    lines = [
        COMMENT_MARKER,
        "## Holo PR QA: PLANNED",
        "",
        f"Preview selected: {preview_url}",
        f"Check planner: {planner}",
        f"Checks selected: {len(tasks)}. Agent attempts per check: {len(profiles)}.",
        "",
        "| Check | Why | Changed paths |",
        "| --- | --- | --- |",
    ]
    for task in tasks:
        paths = ", ".join(task.paths[:8]) if task.paths else "Diff-wide"
        lines.append(f"| {md_escape(task.title)} | {md_escape(task.reason)} | {md_escape(paths)} |")
    lines.extend(["", "| Attempt | Tier | Agent / model | Budget |", "| ---: | --- | --- | --- |"])
    for profile in profiles:
        agent_label = profile.agent if not profile.model else f"{profile.agent} / {profile.model}"
        lines.append(
            f"| {profile.attempt} | {md_escape(profile.tier)} | {md_escape(agent_label)} | "
            f"{profile.max_steps} steps, {profile.max_time_s}s |"
        )
    lines.append("")
    lines.append("_The fast Holo agents are starting first. Balanced and deep tiers run only for checks that need escalation._")
    return "\n".join(lines)


def matrix_entry(task: CheckTask, profile: AttemptProfile) -> dict[str, Any]:
    return {
        "check_id": task.check_id,
        "check_title": task.title[:80],
        "attempt": profile.attempt,
        "tier": profile.tier,
        "agent": profile.agent,
    }


def build_matrix(tasks: list[CheckTask], profiles: list[AttemptProfile], attempt: int | None = None) -> dict[str, list[dict[str, Any]]]:
    include = []
    for task in tasks:
        for profile in profiles:
            if attempt is None or profile.attempt == attempt:
                include.append(matrix_entry(task, profile))
    return {"include": include}


def empty_matrix() -> dict[str, list[dict[str, Any]]]:
    return {"include": []}


def matrix_has_work(matrix: dict[str, list[dict[str, Any]]]) -> bool:
    return bool(matrix.get("include"))


def build_escalation_matrix(plan: dict[str, Any], runs: list[AgentRun], next_attempt: int) -> dict[str, list[dict[str, Any]]]:
    tasks = [task_from_dict(task) for task in plan["tasks"]]
    profiles = [profile_from_dict(profile) for profile in plan["profiles"]]
    next_profile = next((profile for profile in profiles if profile.attempt == next_attempt), None)
    previous_attempt = next_attempt - 1
    if not next_profile:
        return empty_matrix()

    runs_by_task: dict[str, list[AgentRun]] = {}
    for run in runs:
        runs_by_task.setdefault(run.task_id, []).append(run)

    include = []
    for task in tasks:
        previous_run = next(
            (run for run in runs_by_task.get(task.check_id, []) if run.attempt == previous_attempt),
            None,
        )
        if previous_run is not None and should_escalate(previous_run):
            include.append(matrix_entry(task, next_profile))
            continue
        if previous_run is None and next_attempt == 2:
            include.append(matrix_entry(task, next_profile))
            continue
        if previous_run is None and next_attempt == 3:
            fast_run = next(
                (run for run in runs_by_task.get(task.check_id, []) if run.attempt == 1),
                None,
            )
            if fast_run is not None and should_escalate(fast_run):
                include.append(matrix_entry(task, next_profile))
    return {"include": include}


def missing_attempt_status(task_id: str, attempt: int, runs_by_task: dict[str, list[AgentRun]]) -> tuple[str, str]:
    if attempt == 1:
        return "missing_artifact", "This matrix job did not publish a result artifact."
    previous_run = next(
        (run for run in runs_by_task.get(task_id, []) if run.attempt == attempt - 1),
        None,
    )
    if previous_run is not None and not should_escalate(previous_run):
        return "skipped_no_escalation", "Skipped because the previous tier completed without a regression."
    if attempt == 3 and previous_run is None:
        fast_run = next((run for run in runs_by_task.get(task_id, []) if run.attempt == 1), None)
        if fast_run is not None and not should_escalate(fast_run):
            return "skipped_no_escalation", "Skipped because the fast tier completed without a regression."
    return "missing_artifact", "This matrix job was expected to run but did not publish a result artifact."


def make_plan(github: Github, pr: PullRequest, files: list[dict[str, Any]], preview_url: str) -> dict[str, Any]:
    tasks, planner = select_tasks_for_pr(pr, files, env_int("HOLO_MAX_CHECKS", 5))
    profiles = configured_attempt_profiles()
    return {
        "pr": pr_to_dict(pr),
        "preview_url": preview_url,
        "planner": planner,
        "diff_summary": summarize_files(files),
        "tasks": [task_to_dict(task) for task in tasks],
        "profiles": [profile_to_dict(profile) for profile in profiles],
        "matrix": build_matrix(tasks, profiles, attempt=1),
    }


def require_common_env(*, needs_openai: bool = False, needs_holo: bool = False) -> tuple[Github, PullRequest]:
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN are required.")
    if needs_openai and not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI diff-based check planning.")
    if needs_holo and not (os.getenv("HAI_API_KEY") or os.getenv("HOLO_API_KEY")):
        raise RuntimeError("Set HAI_API_KEY or HOLO_API_KEY for Holo agent runs.")
    github = Github(repo, token)
    return github, github.get_pr(get_pr_number())


def command_plan(args: argparse.Namespace) -> int:
    github, pr = require_common_env(needs_openai=True)
    files = github.list_pr_files(pr.number)
    preview_url = discover_preview_url(github, pr)
    plan = make_plan(github, pr, files, preview_url)
    write_json(args.plan_file, plan)
    tasks = [task_from_dict(task) for task in plan["tasks"]]
    profiles = [profile_from_dict(profile) for profile in plan["profiles"]]
    github.try_upsert_comment(pr.number, render_plan_comment(pr, preview_url, plan["planner"], tasks, profiles))
    write_github_output("preview_url", preview_url)
    write_github_output("planner", plan["planner"])
    write_github_output("matrix", json.dumps(plan["matrix"], separators=(",", ":")))
    append_step_summary(render_plan_comment(pr, preview_url, plan["planner"], tasks, profiles).replace(COMMENT_MARKER, ""))
    return 0


def command_run_agent(args: argparse.Namespace) -> int:
    require_common_env(needs_holo=True)
    plan = load_json(args.plan_file)
    pr = pr_from_dict(plan["pr"])
    task = next(task_from_dict(item) for item in plan["tasks"] if item["check_id"] == args.check_id)
    profile = next(profile_from_dict(item) for item in plan["profiles"] if int(item["attempt"]) == int(args.attempt))
    run = run_agent(pr, str(plan["preview_url"]), str(plan["diff_summary"]), task, profile)
    write_json(args.result_file, run_to_dict(run))

    severity = normalize_severity(run.answer)
    regression = "yes" if run.answer.get("regression_detected") else "no"
    agent_label = run.agent if not run.model else f"{run.agent} / {run.model}"
    trajectory = run.agent_view_url or run.session_id or "unavailable"
    trajectory_markdown = f"[open Holo trajectory]({trajectory})" if str(trajectory).startswith("http") else str(trajectory)
    append_step_summary(
        "\n".join(
            [
                f"## {task.title} / {run.tier}",
                "",
                f"- Agent: `{agent_label}`",
                f"- Status: `{run.status}`",
                f"- Severity: `{severity}`",
                f"- Regression detected: `{regression}`",
                f"- Holo trajectory: {trajectory_markdown}",
                "",
                str(run.answer.get("summary") or "No structured summary returned."),
            ]
        )
    )
    print(json.dumps(run_to_dict(run), indent=2, sort_keys=True))
    if should_escalate(run):
        message = (
            f"{task.title} / {run.tier} requires escalation. "
            f"severity={severity}; regression={regression}; trajectory={trajectory}"
        )
        print(f"::error title={github_annotation_escape('Holo agent requires escalation')}::{github_annotation_escape(message)}")
        return 1
    return 0


def find_plan_file(artifacts_dir: str | Path) -> Path:
    matches = list(Path(artifacts_dir).rglob("holo-plan.json"))
    if not matches:
        raise RuntimeError(f"No holo-plan.json found under {artifacts_dir}.")
    return matches[0]


def load_runs(artifacts_dir: str | Path) -> list[AgentRun]:
    runs = []
    for path in Path(artifacts_dir).rglob("result.json"):
        runs.append(run_from_dict(load_json(path)))
    return runs


def command_summarize(args: argparse.Namespace) -> int:
    github, _ = require_common_env()
    plan = load_json(args.plan_file or find_plan_file(args.artifacts_dir))
    pr = pr_from_dict(plan["pr"])
    tasks = [task_from_dict(task) for task in plan["tasks"]]
    profiles = [profile_from_dict(profile) for profile in plan["profiles"]]
    runs = load_runs(args.artifacts_dir)
    runs_by_task: dict[str, list[AgentRun]] = {task.check_id: [] for task in tasks}
    for run in runs:
        runs_by_task.setdefault(run.task_id, []).append(run)

    for task in tasks:
        existing_attempts = {run.attempt for run in runs_by_task[task.check_id]}
        for profile in profiles:
            if profile.attempt not in existing_attempts:
                status, summary = missing_attempt_status(task.check_id, profile.attempt, runs_by_task)
                runs_by_task[task.check_id].append(
                    AgentRun(
                        task_id=task.check_id,
                        attempt=profile.attempt,
                        tier=profile.tier,
                        agent=profile.agent,
                        model=profile.model,
                        status=status,
                        elapsed_s=0,
                        session_id=None,
                        agent_view_url=None,
                        answer={
                            "success": False,
                            "regression_detected": False,
                            "severity": "none",
                            "confidence": 0,
                            "summary": summary,
                            "evidence": [],
                        },
                        error=None if status == "skipped_no_escalation" else "missing result artifact",
                    )
                )

    summaries = [
        summarize_task(task, sorted(runs_by_task[task.check_id], key=lambda run: run.attempt))
        for task in tasks
    ]
    comment = render_comment(pr, str(plan["preview_url"]), str(plan["planner"]), summaries)
    github.try_upsert_comment(pr.number, comment)
    append_step_summary(comment.replace(COMMENT_MARKER, ""))
    if any(summary["big_regression"] for summary in summaries):
        return 1
    return 0


def command_matrix(args: argparse.Namespace) -> int:
    plan = load_json(args.plan_file or find_plan_file(args.artifacts_dir))
    runs = load_runs(args.artifacts_dir) if Path(args.artifacts_dir).exists() else []
    matrix = build_escalation_matrix(plan, runs, args.attempt)
    matrix_json = json.dumps(matrix, separators=(",", ":"))
    write_github_output("matrix", matrix_json)
    write_github_output("has_work", "true" if matrix_has_work(matrix) else "false")
    append_step_summary(
        f"Prepared attempt {args.attempt} matrix with {len(matrix.get('include', []))} check(s)."
    )
    print(matrix_json)
    return 0


def command_all() -> int:
    repo = os.getenv("GITHUB_REPOSITORY")
    token = os.getenv("GITHUB_TOKEN")
    if not repo or not token:
        raise RuntimeError("GITHUB_REPOSITORY and GITHUB_TOKEN are required.")
    if not os.getenv("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY is required for OpenAI diff-based check planning.")
    if not (os.getenv("HAI_API_KEY") or os.getenv("HOLO_API_KEY")):
        raise RuntimeError("Set HAI_API_KEY or HOLO_API_KEY for Holo agent runs.")

    github = Github(repo, token)
    pr = github.get_pr(get_pr_number())
    files = github.list_pr_files(pr.number)
    preview_url = discover_preview_url(github, pr)
    tasks, planner = select_tasks_for_pr(pr, files, env_int("HOLO_MAX_CHECKS", 5))

    print(f"Testing {preview_url} for PR #{pr.number} with {len(tasks)} checks x 3 agents. Planner: {planner}")
    summaries = run_all_checks(pr, preview_url, files, tasks)
    github.try_upsert_comment(pr.number, render_comment(pr, preview_url, planner, summaries))

    if any(summary["big_regression"] for summary in summaries):
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Holo PR QA orchestration.")
    subparsers = parser.add_subparsers(dest="command")

    plan_parser = subparsers.add_parser("plan", help="Create the five-check plan and matrix.")
    plan_parser.add_argument("--plan-file", default="holo-plan.json")

    run_parser = subparsers.add_parser("run-agent", help="Run one Holo matrix cell.")
    run_parser.add_argument("--plan-file", default="holo-plan.json")
    run_parser.add_argument("--check-id", required=True)
    run_parser.add_argument("--attempt", required=True, type=int)
    run_parser.add_argument("--result-file", default="result.json")

    summarize_parser = subparsers.add_parser("summarize", help="Summarize all matrix result artifacts.")
    summarize_parser.add_argument("--artifacts-dir", default="artifacts")
    summarize_parser.add_argument("--plan-file", default="")

    matrix_parser = subparsers.add_parser("matrix", help="Build an escalation matrix from prior results.")
    matrix_parser.add_argument("--artifacts-dir", default="artifacts")
    matrix_parser.add_argument("--plan-file", default="")
    matrix_parser.add_argument("--attempt", required=True, type=int)

    subparsers.add_parser("all", help="Run the legacy single-job orchestration.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "plan":
        return command_plan(args)
    if args.command == "run-agent":
        return command_run_agent(args)
    if args.command == "summarize":
        return command_summarize(args)
    if args.command == "matrix":
        return command_matrix(args)
    return command_all()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # noqa: BLE001 - make CI failure actionable.
        print(f"holo_pr_qa failed: {exc}", file=sys.stderr)
        raise SystemExit(2)
