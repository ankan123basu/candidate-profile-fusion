"""
GitHub extractor — fetches candidate data from the GitHub REST API.

Uses unauthenticated requests by default (60 req/hr).
Set GITHUB_TOKEN env var for higher rate limits (5000 req/hr).

Fields extracted: name, bio (→ summary), public repos, top languages,
email (if public), location, company, blog/website.
"""

from __future__ import annotations

import logging
import os
import json
from pathlib import Path
from typing import Dict, List
from collections import Counter

import requests

from src.schema import RawField, ExtractionMethod
from src.extract.base import BaseExtractor

logger = logging.getLogger(__name__)

GITHUB_API_BASE = "https://api.github.com"


class GithubExtractor(BaseExtractor):
    """Extract candidate data from GitHub user profiles."""

    source_name = "github"

    def __init__(self):
        self.session = requests.Session()
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            self.session.headers["Authorization"] = f"token {token}"
            logger.info("GitHub extractor using authenticated requests")
        self.session.headers["Accept"] = "application/vnd.github.v3+json"
        self.session.headers["User-Agent"] = "candidate-etl/1.0"

    def extract(self, source_path: str | Path) -> Dict[str, List[RawField]]:
        """
        Extract from GitHub.

        source_path can be:
          - A GitHub username string (e.g. "octocat")
          - A file containing one username per line
          - A JSON file with GitHub usernames/URLs
        """
        source_path = Path(source_path) if not str(source_path).startswith("http") else source_path
        results: Dict[str, List[RawField]] = {}

        usernames = self._resolve_usernames(source_path)
        for username in usernames:
            try:
                fields = self._extract_user(username)
                if fields:
                    # Use email as key if available, else github username
                    candidate_key = None
                    for f in fields:
                        if f.field == "email" and f.value:
                            candidate_key = str(f.value).lower().strip()
                            break
                    if candidate_key is None:
                        candidate_key = f"github_{username}"
                    results[candidate_key] = fields
            except Exception as e:
                logger.warning("Error extracting GitHub user %s: %s", username, e)
                continue

        logger.info("GitHub extractor: extracted %d candidates", len(results))
        return results

    def _resolve_usernames(self, source_path) -> list[str]:
        """Resolve usernames from various input formats."""
        if isinstance(source_path, str) and not Path(source_path).exists():
            # Treat as a single username or URL
            return [self._url_to_username(source_path)]

        path = Path(source_path)
        if not path.exists():
            logger.error("GitHub source file not found: %s", source_path)
            return []

        # JSON file with list of usernames or objects
        if path.suffix == ".json":
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, list):
                    usernames = []
                    for item in data:
                        if isinstance(item, str):
                            usernames.append(self._url_to_username(item))
                        elif isinstance(item, dict):
                            for key in ("username", "github", "github_url", "login"):
                                if key in item:
                                    usernames.append(self._url_to_username(str(item[key])))
                                    break
                    return usernames
            except (json.JSONDecodeError, Exception) as e:
                logger.error("Error reading GitHub JSON %s: %s", path, e)
                return []

        # Plain text file — one username per line
        try:
            with open(path, "r", encoding="utf-8") as f:
                return [
                    self._url_to_username(line.strip())
                    for line in f if line.strip() and not line.startswith("#")
                ]
        except Exception as e:
            logger.error("Error reading GitHub usernames from %s: %s", path, e)
            return []

    def _url_to_username(self, value: str) -> str:
        """Extract username from a GitHub URL or return as-is."""
        value = value.strip().rstrip("/")
        if "github.com/" in value:
            return value.split("github.com/")[-1].split("/")[0]
        return value

    def _extract_user(self, username: str) -> List[RawField]:
        """Fetch a single GitHub user's profile and repos."""
        fields: List[RawField] = []
        source_id = f"user_{username}"

        # Fetch user profile
        resp = self.session.get(f"{GITHUB_API_BASE}/users/{username}", timeout=10)
        if resp.status_code == 404:
            logger.warning("GitHub user not found: %s", username)
            return fields
        if resp.status_code == 403:
            logger.error("GitHub API rate limit exceeded")
            return fields
        resp.raise_for_status()
        user = resp.json()

        # Map profile fields
        profile_map = {
            "name": "full_name",
            "email": "email",
            "company": "current_company",
            "location": "location",
            "bio": "summary",
            "blog": "linkedin_url",  # sometimes LinkedIn URL goes here
            "html_url": "github_url",
        }

        for api_key, canonical_field in profile_map.items():
            value = user.get(api_key)
            if value and str(value).strip():
                # Special: only map blog to linkedin_url if it's actually LinkedIn
                if api_key == "blog":
                    if "linkedin.com" not in str(value).lower():
                        continue  # skip non-LinkedIn blog URLs

                fields.append(RawField(
                    field=canonical_field,
                    value=str(value).strip(),
                    source=self.source_name,
                    source_id=source_id,
                    extraction_method=ExtractionMethod.API,
                ))

        # Always add github_url
        if user.get("html_url"):
            fields.append(RawField(
                field="github_url",
                value=user["html_url"],
                source=self.source_name,
                source_id=source_id,
                extraction_method=ExtractionMethod.API,
            ))

        # Fetch repos to get languages (top skills)
        try:
            repos_resp = self.session.get(
                f"{GITHUB_API_BASE}/users/{username}/repos",
                params={"per_page": 30, "sort": "updated"},
                timeout=10,
            )
            if repos_resp.status_code == 200:
                repos = repos_resp.json()
                lang_counter: Counter = Counter()
                for repo in repos:
                    if isinstance(repo, dict) and repo.get("language"):
                        lang_counter[repo["language"]] += 1

                if lang_counter:
                    # Top languages as skills
                    top_languages = [lang for lang, _ in lang_counter.most_common(10)]
                    fields.append(RawField(
                        field="skills",
                        value=top_languages,
                        source=self.source_name,
                        source_id=source_id,
                        extraction_method=ExtractionMethod.API,
                    ))

                # Repo count as a rough proxy (not directly a canonical field,
                # but useful metadata)
                if repos:
                    fields.append(RawField(
                        field="summary",
                        value=f"{len(repos)} public repositories on GitHub. " +
                              (user.get("bio", "") or ""),
                        source=self.source_name,
                        source_id=source_id,
                        extraction_method=ExtractionMethod.HEURISTIC,
                    ))
        except Exception as e:
            logger.warning("Error fetching repos for %s: %s", username, e)

        return fields
