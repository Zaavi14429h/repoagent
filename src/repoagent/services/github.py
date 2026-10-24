from __future__ import annotations

import httpx

from repoagent.models import PullRequestResult


class GitHubService:
    def __init__(self, token: str | None) -> None:
        self.token = token

    async def create_pull_request(
        self,
        repository: str,
        head: str,
        base: str,
        title: str,
        body: str,
        draft: bool,
    ) -> PullRequestResult:
        if not self.token:
            return PullRequestResult(created=False, error="GITHUB_TOKEN is not configured")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"https://api.github.com/repos/{repository}/pulls",
                headers=headers,
                json={
                    "title": title,
                    "head": head,
                    "base": base,
                    "body": body,
                    "draft": draft,
                },
            )
        if response.status_code not in {200, 201}:
            message = response.json().get("message", response.text[:500])
            return PullRequestResult(created=False, title=title, error=str(message))
        payload = response.json()
        return PullRequestResult(
            created=True,
            number=payload["number"],
            url=payload["html_url"],
            title=payload["title"],
        )
