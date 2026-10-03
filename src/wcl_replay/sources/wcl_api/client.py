# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Warcraft Logs API v2 client: OAuth client-credentials, GraphQL, event pagination, disk cache."""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ...core.models import Fight, FightData
from ..local_log.index import cache_dir
from .convert import convert, fight_from_meta, pull_numbers

CACHE_VERSION = 1

REPORT_QUERY = """
query($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      endTime
      fights {
        id encounterID name difficulty kill startTime endTime size
        enemyNPCs { id gameID }
        enemyPets { id gameID }
        friendlyPlayers
      }
      masterData {
        actors { id name type subType icon petOwner gameID server }
        abilities { gameID name }
      }
    }
  }
}
"""

EVENTS_QUERY = """
query($code: String!, $fight: [Int]!, $start: Float!, $end: Float!, $hostility: HostilityType!) {
  reportData {
    report(code: $code) {
      events(fightIDs: $fight, startTime: $start, endTime: $end, hostilityType: $hostility,
             includeResources: true, limit: 10000) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""


class WclError(RuntimeError):
    pass


RATE_LIMIT_QUERY = """
query {
  rateLimitData {
    limitPerHour
    pointsSpentThisHour
    pointsResetIn
  }
}
"""


def parse_rate_limit(data: dict) -> tuple[float, int, int]:
    """``(points spent this hour, hourly limit, seconds until the counter resets)``."""
    block = data.get("rateLimitData") or {}
    return (
        float(block.get("pointsSpentThisHour") or 0),
        int(block.get("limitPerHour") or 0),
        int(block.get("pointsResetIn") or 0),
    )


@dataclass
class ReportInfo:
    code: str
    title: str
    fights: list[Fight]
    raw: dict


class WclClient:
    def __init__(
        self, client_id: str, client_secret: str, host: str = "www.warcraftlogs.com", timeout: float = 60.0
    ):
        if not client_id or not client_secret:
            raise WclError("未配置 WCL API 的 Client ID / Secret（工具栏 → WCL API 设置…）")
        self.client_id = client_id
        self.client_secret = client_secret
        self.host = host or "www.warcraftlogs.com"
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "wcl-replay/0.1"})
        self._token: tuple[str, float] | None = None
        self._reports: dict[str, tuple[float, dict]] = {}

    # -- auth / transport ---------------------------------------------------------------------

    def _token_file(self):
        key = hashlib.sha1(f"{self.host}|{self.client_id}".encode()).hexdigest()[:16]
        return cache_dir() / "wcl" / f"token-{key}.json"

    def token(self, force: bool = False) -> str:
        now = time.time()
        if not force and self._token and self._token[1] > now + 60:
            return self._token[0]
        path = self._token_file()
        if not force and path.exists():
            try:
                tok = json.loads(path.read_text("utf-8"))
                if tok["expires_at"] > now + 60:
                    self._token = (tok["access_token"], tok["expires_at"])
                    return tok["access_token"]
            except (ValueError, KeyError):
                pass
        try:
            r = self.http.post(
                f"https://{self.host}/oauth/token",
                data={"grant_type": "client_credentials"},
                auth=(self.client_id, self.client_secret),
            )
        except httpx.HTTPError as exc:
            raise WclError(f"连接 WCL 失败：{exc}") from exc
        if r.status_code in (400, 401):
            raise WclError("WCL 认证失败：请检查 Client ID / Secret")
        r.raise_for_status()
        body = r.json()
        self._token = (body["access_token"], now + float(body.get("expires_in", 3600)))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"access_token": self._token[0], "expires_at": self._token[1]}), "utf-8")
        return self._token[0]

    def query(self, q: str, variables: dict) -> dict:
        for attempt in range(2):
            try:
                r = self.http.post(
                    f"https://{self.host}/api/v2/client",
                    json={"query": q, "variables": variables},
                    headers={"Authorization": f"Bearer {self.token(force=attempt > 0)}"},
                )
            except httpx.HTTPError as exc:
                raise WclError(f"WCL 请求失败：{exc}") from exc
            if r.status_code == 401 and attempt == 0:
                continue
            if r.status_code == 429:
                raise WclError("WCL API 请求过于频繁（达到速率上限），请稍后再试")
            if r.status_code >= 400:
                raise WclError(f"WCL API 错误 {r.status_code}: {r.text[:300]}")
            body = r.json()
            if body.get("errors"):
                raise WclError(
                    "WCL GraphQL 错误：" + "; ".join(e.get("message", "?") for e in body["errors"])
                )
            return body["data"]
        raise WclError("WCL 认证失败")

    def rate_limit(self) -> tuple[float, int, int]:
        return parse_rate_limit(self.query(RATE_LIMIT_QUERY, {}))

    # -- reports ------------------------------------------------------------------------------

    def report(self, code: str) -> dict:
        hit = self._reports.get(code)
        if hit and time.time() - hit[0] < 300:
            return hit[1]
        rep = self.query(REPORT_QUERY, {"code": code})["reportData"]["report"]
        if rep is None:
            raise WclError(f"找不到报告 {code}（报告不存在或为私有）")
        self._reports[code] = (time.time(), rep)
        return rep

    def fights(self, code: str) -> ReportInfo:
        rep = self.report(code)
        pulls = pull_numbers(rep.get("fights") or [])
        fights = [fight_from_meta(rep, f, pulls.get(int(f["id"]), 0)) for f in rep.get("fights") or []]
        return ReportInfo(code, rep.get("title") or code, fights, rep)

    def events(
        self, code: str, fight: dict, progress: Callable[[float, str], None] | None = None
    ) -> list[dict]:
        start, end = float(fight["startTime"]), float(fight["endTime"])
        span = max(1.0, end - start)
        out: list[dict] = []
        seen: set[str] = set()
        views = ("Friendlies", "Enemies")
        for vi, view in enumerate(views):
            cursor: float | None = start
            while cursor is not None:
                data = self.query(
                    EVENTS_QUERY,
                    {
                        "code": code,
                        "fight": [int(fight["id"])],
                        "start": cursor,
                        "end": end,
                        "hostility": view,
                    },
                )
                page = data["reportData"]["report"]["events"]
                for ev in page.get("data") or []:
                    key = json.dumps(ev, sort_keys=True)
                    if key not in seen:
                        seen.add(key)
                        out.append(ev)
                nxt = page.get("nextPageTimestamp")
                cursor = float(nxt) if nxt is not None and nxt > cursor else None
                if progress:
                    done = ((cursor if cursor is not None else end) - start) / span
                    progress(
                        (vi + done) / len(views),
                        f"下载事件（{'友方' if vi == 0 else '敌方'}视角）… {len(out)} 条",
                    )
        out.sort(key=lambda ev: ev.get("timestamp", 0))
        return out

    def fight_data(
        self, code: str, fight_id: int, progress: Callable[[float, str], None] | None = None
    ) -> FightData:
        rep = self.report(code)
        fight = next((f for f in rep.get("fights") or [] if int(f["id"]) == fight_id), None)
        if fight is None:
            raise WclError(f"报告 {code} 中没有 fight {fight_id}")
        path = cache_dir() / "wcl" / f"{code}-{fight_id}.json.gz"
        events = None
        if path.exists():
            try:
                with gzip.open(path, "rt", encoding="utf-8") as fh:
                    blob = json.load(fh)
                if blob.get("version") == CACHE_VERSION and blob.get("endTime") == fight.get("endTime"):
                    events = blob["events"]
                    if progress:
                        progress(1.0, "使用本地缓存")
            except (OSError, ValueError, KeyError):
                events = None
        if events is None:
            events = self.events(code, fight, progress)
            path.parent.mkdir(parents=True, exist_ok=True)
            with gzip.open(path, "wt", encoding="utf-8") as fh:
                json.dump({"version": CACHE_VERSION, "endTime": fight.get("endTime"), "events": events}, fh)
        pulls = pull_numbers(rep.get("fights") or [])
        return convert(rep, fight, events, pulls.get(fight_id, 0), source=f"wcl:{code}#{fight_id}")
