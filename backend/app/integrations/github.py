import asyncio
import re
from typing import Any

import httpx

from .base import Control, Field, Integration, IntegrationError, Item, Option, TileData, action, http, tool

API = "https://api.github.com"
REPO_NAME = re.compile(r"^[\w.-]+/[\w.-]+$")
WORKFLOW_NAME = re.compile(r"^[\w.-]+$")
RUN_STATUS = {"success": ("ok", "Passing"), "failure": ("error", "Failing"), "timed_out": ("error", "Timed out"),
              "cancelled": ("warn", "Cancelled"), "action_required": ("warn", "Needs action")}


def _check_repo(repo: str) -> str:
    repo = (repo or "").strip()
    if not REPO_NAME.match(repo):
        raise IntegrationError("Use the owner/name form for the repository, like octocat/hello-world.")
    return repo


def _run_status(run: dict) -> tuple[str, str]:
    if run["status"] != "completed":
        return "warn", "Running"
    return RUN_STATUS.get(run.get("conclusion") or "", ("idle", (run.get("conclusion") or "Done").title()))


class GitHub(Integration):
    id = "github"
    name = "GitHub repo"
    description = "CI status and open pull requests for a repository, with an optional workflow button."
    icon = "si:github"
    requires = ("GITHUB_TOKEN",)
    refresh = 120
    default_size = "2x1"
    fields = [
        Field(key="repo", label="Repository", type="picker", required=True, placeholder="owner/name"),
        Field(key="workflow", label="Workflow to run", placeholder="deploy.yml",
              help="Optional. Adds a button that starts this workflow."),
        Field(key="ref", label="Branch for the workflow", placeholder="Default branch"),
    ]

    async def _request(self, method: str, path: str, raw: bool = False, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {self.env('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json",
                   "X-GitHub-Api-Version": "2022-11-28"}
        try:
            async with http(base_url=API, headers=headers) as client:
                response = await client.request(method, path, **kwargs)
        except httpx.HTTPError:
            raise IntegrationError("Couldn't reach GitHub.")
        if response.status_code == 401:
            raise IntegrationError("GitHub rejected GITHUB_TOKEN.")
        if response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0":
            raise IntegrationError("GitHub's rate limit is used up; it resets within the hour.")
        if response.status_code in (403, 404):
            raise IntegrationError(f"GitHub can't find {path}, or GITHUB_TOKEN can't access it.")
        if response.is_error:
            raise IntegrationError(f"GitHub returned HTTP {response.status_code}: {response.text[:200]}")
        if raw:
            return response
        return response.json() if response.content else None

    async def _open_pr_count(self, repo: str, first_page: list[dict]) -> int | None:
        """Exact count of open PRs. Past one page of 100, read the last page number from the Link
        header; None if that extra request fails (the tile then says "100+")."""
        if len(first_page) < 100:
            return len(first_page)
        try:
            response = await self._request("GET", f"/repos/{repo}/pulls", raw=True,
                                           params={"state": "open", "per_page": 1})
        except IntegrationError:
            return None
        last = re.search(r'[?&]page=(\d+)>; rel="last"', response.headers.get("link", ""))
        return int(last.group(1)) if last else None

    async def _overview(self, repo: str) -> tuple[dict, list[dict], list[dict]]:
        info, runs, pulls = await asyncio.gather(
            self._request("GET", f"/repos/{repo}"),
            self._request("GET", f"/repos/{repo}/actions/runs", params={"per_page": 3}),
            self._request("GET", f"/repos/{repo}/pulls", params={"state": "open", "per_page": 100}),
        )
        return info, runs["workflow_runs"], pulls

    async def fetch(self, config):
        repo = _check_repo(config.get("repo"))
        info, runs, pulls = await self._overview(repo)
        status, readout = _run_status(runs[0]) if runs else ("idle", "No CI runs")
        pr_count = await self._open_pr_count(repo, pulls)
        # GitHub counts PRs as issues; without an exact PR count the issue count is unknown.
        counts = (f"{pr_count} open PRs, {info['open_issues_count'] - pr_count} issues" if pr_count is not None
                  else f"{len(pulls)}+ open PRs")
        items = [Item(label=run.get("display_title") or run["name"], detail=f"{run['name']} on {run['head_branch']}",
                      status=_run_status(run)[0], url=run["html_url"]) for run in runs]
        controls = []
        if config.get("workflow"):
            controls.append(Control(action="dispatch", label=f"Run {config['workflow']}"))
        return TileData(status=status, value=readout, detail=counts,
                        items=items, controls=controls, url=info["html_url"])

    async def _dispatch(self, repo: str, workflow: str, ref: str) -> str:
        if not WORKFLOW_NAME.match(workflow or ""):
            raise IntegrationError("Use the workflow's file name, like deploy.yml.")
        if not ref:
            ref = (await self._request("GET", f"/repos/{repo}"))["default_branch"]
        await self._request("POST", f"/repos/{repo}/actions/workflows/{workflow}/dispatches", json={"ref": ref})
        return ref

    @action
    async def dispatch(self, config, params):
        await self._dispatch(_check_repo(config.get("repo")), config.get("workflow", ""), config.get("ref", ""))
        await asyncio.sleep(2)  # give GitHub a moment to register the run
        return await self.fetch(config)

    async def options(self, field, query):
        if field != "repo":
            return []
        repos = await self._request("GET", "/user/repos", params={"per_page": 100, "sort": "pushed"})
        return [Option(value=r["full_name"], label=r["full_name"], detail=r.get("description"))
                for r in repos if query.lower() in r["full_name"].lower()][:50]

    # Tools for Hermes

    @tool()
    async def list_repos(self) -> list[dict]:
        """List the user's GitHub repositories, most recently pushed first."""
        repos = await self._request("GET", "/user/repos", params={"per_page": 50, "sort": "pushed"})
        return [{"repo": r["full_name"], "description": r.get("description"), "private": r["private"],
                 "pushed_at": r["pushed_at"]} for r in repos]

    @tool()
    async def repo_status(self, repo: str) -> dict:
        """Summarize a repository (owner/name): latest CI runs and open pull requests."""
        info, runs, pulls = await self._overview(_check_repo(repo))
        return {
            "repo": info["full_name"], "default_branch": info["default_branch"], "url": info["html_url"],
            "ci_runs": [{"workflow": r["name"], "title": r.get("display_title"), "branch": r["head_branch"],
                         "status": _run_status(r)[1], "url": r["html_url"]} for r in runs],
            "open_pull_requests": [{"number": p["number"], "title": p["title"], "author": p["user"]["login"]}
                                   for p in pulls],
        }

    @tool()
    async def list_issues(self, repo: str, state: str = "open") -> list[dict]:
        """List issues (not pull requests) in a repository. state is open, closed or all."""
        issues = await self._request("GET", f"/repos/{_check_repo(repo)}/issues",
                                     params={"state": state, "per_page": 50})
        return [{"number": i["number"], "title": i["title"], "state": i["state"], "url": i["html_url"]}
                for i in issues if "pull_request" not in i]

    @tool(write=True)
    async def create_issue(self, repo: str, title: str, body: str = "") -> dict:
        """Open a new issue in a repository."""
        issue = await self._request("POST", f"/repos/{_check_repo(repo)}/issues", json={"title": title, "body": body})
        return {"number": issue["number"], "url": issue["html_url"]}

    @tool(write=True)
    async def run_workflow(self, repo: str, workflow: str, ref: str = "") -> str:
        """Start a GitHub Actions workflow by file name (e.g. deploy.yml). ref defaults to the default branch."""
        ref = await self._dispatch(_check_repo(repo), workflow, ref)
        return f"Started {workflow} on {ref}."


integration = GitHub()
