# zotero_web.py
"""Zotero Web API client for automatic literature import."""

from __future__ import annotations

import time
import uuid
from urllib.parse import quote

import requests


ZOTERO_API_BASE = "https://api.zotero.org"
MAX_WRITE_BATCH = 50


class ZoteroWebError(RuntimeError):
    pass


class ZoteroWebClient:
    def __init__(self, api_key: str, library_type: str, library_id: str,
                 base_url: str = ZOTERO_API_BASE, timeout: int = 20):
        if library_type not in {"user", "group"}:
            raise ValueError("library_type must be 'user' or 'group'")
        if not api_key:
            raise ValueError("Zotero API key is required")
        if not library_id:
            raise ValueError("Zotero library id is required")
        self.api_key = api_key
        self.library_type = library_type
        self.library_id = str(library_id)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    @property
    def library_path(self) -> str:
        plural = "users" if self.library_type == "user" else "groups"
        return f"{plural}/{self.library_id}"

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{self.library_path}/{path.lstrip('/')}"

    def _headers(self, write_token: str | None = None) -> dict:
        headers = {
            "Zotero-API-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if write_token:
            headers["Zotero-Write-Token"] = write_token
        return headers

    def _request(self, method: str, path: str, **kwargs):
        headers = kwargs.pop("headers", {})
        merged = self._headers()
        merged.update(headers)
        for attempt in range(3):
            resp = requests.request(
                method,
                self._url(path),
                headers=merged,
                timeout=self.timeout,
                **kwargs,
            )
            if resp.status_code == 429 and attempt < 2:
                retry_after = resp.headers.get("Retry-After", "1")
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 1.0
                time.sleep(delay)
                continue
            if resp.status_code >= 400:
                raise ZoteroWebError(f"Zotero API {method} {path} failed: {resp.status_code} {resp.text[:500]}")
            return resp
        raise ZoteroWebError(f"Zotero API {method} {path} failed after retries")

    def _get_all(self, path: str, limit: int = 100) -> list[dict]:
        start = 0
        items = []
        while True:
            sep = "&" if "?" in path else "?"
            resp = self._request("GET", f"{path}{sep}limit={limit}&start={start}")
            batch = resp.json()
            if not isinstance(batch, list):
                raise ZoteroWebError(f"Unexpected Zotero response for {path}")
            items.extend(batch)
            if len(batch) < limit:
                break
            start += limit
        return items

    def list_collections(self) -> list[dict]:
        return self._get_all("collections")

    def create_collection(self, name: str, parent_key: str | None = None) -> str:
        payload = {"name": name}
        if parent_key:
            payload["parentCollection"] = parent_key
        resp = self._request(
            "POST",
            "collections",
            headers={"Zotero-Write-Token": uuid.uuid4().hex},
            json=[payload],
        )
        data = resp.json()
        successful = data.get("successful", {}) if isinstance(data, dict) else {}
        first = successful.get("0") or successful.get(0)
        if not first or "key" not in first:
            failed = data.get("failed", {}) if isinstance(data, dict) else {}
            raise ZoteroWebError(f"Collection creation failed: {failed or data}")
        return first["key"]

    def ensure_collection_path(self, path: str, create: bool = True) -> str:
        parts = [p.strip() for p in path.split(">") if p.strip()]
        if not parts:
            raise ValueError("collection path is required")

        collections = self.list_collections()
        children = {}
        for col in collections:
            data = col.get("data", {})
            parent = data.get("parentCollection") or ""
            children.setdefault(parent, {})[data.get("name", "")] = col.get("key")

        parent = ""
        parent_key = None
        for part in parts:
            key = children.get(parent, {}).get(part)
            if not key:
                if not create:
                    raise ZoteroWebError(f"Collection does not exist: {' > '.join(parts)}")
                key = self.create_collection(part, parent_key)
                children.setdefault(parent, {})[part] = key
                children.setdefault(key, {})
            parent = key
            parent_key = key
        return parent_key

    def find_existing_dois(self, dois: set[str]) -> set[str]:
        existing = set()
        for doi in sorted(d for d in dois if d):
            query = quote(f'DOI:"{doi}"')
            try:
                rows = self._get_all(f"items?itemType=journalArticle&q={query}")
            except ZoteroWebError:
                continue
            for row in rows:
                value = str(row.get("data", {}).get("DOI", "")).strip().lower()
                if value == doi.lower():
                    existing.add(doi.lower())
        return existing

    def create_items(self, papers: list[dict], collection_key: str, tags: list[str] | None = None) -> dict:
        items = [paper_to_zotero_item(p, collection_key, tags or []) for p in papers]
        successful = {}
        failed = {}
        for offset in range(0, len(items), MAX_WRITE_BATCH):
            batch = items[offset:offset + MAX_WRITE_BATCH]
            resp = self._request(
                "POST",
                "items",
                headers={"Zotero-Write-Token": uuid.uuid4().hex},
                json=batch,
            )
            payload = resp.json()
            for key, value in (payload.get("successful", {}) or {}).items():
                successful[str(offset + int(key))] = value
            for key, value in (payload.get("failed", {}) or {}).items():
                failed[str(offset + int(key))] = value
        return {"successful": successful, "failed": failed}

    def create_attachment_items(self, attachments: list[dict]) -> dict:
        successful = {}
        failed = {}
        for offset in range(0, len(attachments), MAX_WRITE_BATCH):
            batch = attachments[offset:offset + MAX_WRITE_BATCH]
            resp = self._request(
                "POST",
                "items",
                headers={"Zotero-Write-Token": uuid.uuid4().hex},
                json=batch,
            )
            payload = resp.json()
            for key, value in (payload.get("successful", {}) or {}).items():
                successful[str(offset + int(key))] = value
            for key, value in (payload.get("failed", {}) or {}).items():
                failed[str(offset + int(key))] = value
        return {"successful": successful, "failed": failed}



def split_author_name(name: str) -> dict:
    clean = " ".join(str(name or "").split())
    if not clean:
        return {}
    if "," in clean:
        last, first = [p.strip() for p in clean.split(",", 1)]
        return {"creatorType": "author", "firstName": first, "lastName": last}
    parts = clean.split()
    if len(parts) >= 2:
        return {"creatorType": "author", "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}
    return {"creatorType": "author", "name": clean}


def paper_to_zotero_item(paper: dict, collection_key: str, tags: list[str]) -> dict:
    ext = paper.get("externalIds", {}) or {}
    doi = str(ext.get("DOI", "") or "").strip()
    journal = paper.get("journal", {}) or {}
    jname = journal.get("name", "") if isinstance(journal, dict) else str(journal)
    item = {
        "itemType": "journalArticle",
        "title": paper.get("title", "") or "",
        "creators": [],
        "date": str(paper.get("year", "") or ""),
        "publicationTitle": jname,
        "DOI": doi if not doi.startswith("PMID:") else "",
        "abstractNote": paper.get("abstract", "") or "",
        "tags": [{"tag": tag} for tag in paper.get("_zotero_tags", tags) if tag],
        "collections": [collection_key] if collection_key else [],
    }
    if doi and not doi.startswith("PMID:"):
        item["url"] = f"https://doi.org/{doi}"
    elif doi.startswith("PMID:"):
        item["extra"] = doi

    for author in paper.get("authors", []) or []:
        name = author.get("name", "") if isinstance(author, dict) else str(author)
        creator = split_author_name(name)
        if creator:
            item["creators"].append(creator)
    return item


def wait_for_local_dois(fetch_existing_dois, dois: set[str], timeout: int = 120, interval: int = 5) -> tuple[set[str], set[str]]:
    deadline = time.time() + timeout
    target = {d.lower() for d in dois if d}
    found = set()
    while time.time() <= deadline:
        found = target & {d.lower() for d in fetch_existing_dois()}
        if found == target:
            break
        time.sleep(interval)
    return found, target - found
