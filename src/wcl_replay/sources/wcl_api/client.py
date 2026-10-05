# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Warcraft Logs API v2 client: OAuth client-credentials, GraphQL, event pagination, disk cache."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass

import httpx

from ...core.cancellation import check_cancelled
from ...core.models import Fight, FightData
from ...storage import cache_dir, cache_warning, write_json
from .convert import convert, fight_from_meta, pull_numbers
from .urls import normalize_host

REPORT_QUERY = """
query($code: String!) {
  reportData {
    report(code: $code) {
      title
      startTime
      endTime
      revision
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


@dataclass(slots=True)
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
        self.host = normalize_host(host or "www.warcraftlogs.com")
        self.http = httpx.Client(timeout=timeout, headers={"User-Agent": "wcl-replay/0.1"})
        self._token: tuple[str, float] | None = None
        self._reports: dict[str, tuple[float, dict]] = {}

    def close(self) -> None:
        self.http.close()

    # -- auth / transport ---------------------------------------------------------------------

    def _token_file(self):
        key = hashlib.sha1(f"{self.host}|{self.client_id}".encode()).hexdigest()[:16]
        return cache_dir() / "wcl" / f"token-{key}.json"

    def token(self, force: bool = False) -> str:
        check_cancelled()
        now = time.time()
        if not force and self._token and self._token[1] > now + 60:
            return self._token[0]
        path = self._token_file()
        if not force:
            try:
                tok = json.loads(path.read_text("utf-8"))
                if (
                    isinstance(tok, dict)
                    and isinstance(tok.get("access_token"), str)
                    and isinstance(tok.get("expires_at"), (int, float))
                    and tok["expires_at"] > now + 60
                ):
                    self._token = (tok["access_token"], tok["expires_at"])
                    return tok["access_token"]
            except (OSError, ValueError, KeyError, TypeError):
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
        try:
            write_json(path, {"access_token": self._token[0], "expires_at": self._token[1]})
        except OSError as exc:
            cache_warning(exc)
        return self._token[0]

    def query(self, q: str, variables: dict) -> dict:
        for attempt in range(2):
            check_cancelled()
            access_token = self.token(force=attempt > 0)
            check_cancelled()
            try:
                r = self.http.post(
                    f"https://{self.host}/api/v2/client",
                    json={"query": q, "variables": variables},
                    headers={"Authorization": f"Bearer {access_token}"},
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
            check_cancelled()
            if body.get("errors"):
                raise WclError(
                    "WCL GraphQL 错误：" + "; ".join(e.get("message", "?") for e in body["errors"])
                )
            return body["data"]
        raise WclError("WCL 认证失败")

    def rate_limit(self) -> tuple[float, int, int]:
        return parse_rate_limit(self.query(RATE_LIMIT_QUERY, {}))

    # -- reports ------------------------------------------------------------------------------

    def report(self, code: str, *, force_refresh: bool = False) -> dict:
        check_cancelled()
        hit = self._reports.get(code)
        if not force_refresh and hit and 0 <= time.time() - hit[0] < 300:
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
        from ...bosses.base import WclSlice
        from .events import download

        slices = tuple(
            WclSlice(view, "All", hostility=view, resources=True) for view in ("Friendlies", "Enemies")
        )
        return download(self, code, fight, slices, progress=progress)

    def fight_data(
        self,
        code: str,
        fight_id: int,
        progress: Callable[[float, str], None] | None = None,
        *,
        force_refresh: bool = False,
    ) -> FightData:
        rep = self.report(code, force_refresh=force_refresh)
        fight = next((f for f in rep.get("fights") or [] if int(f["id"]) == fight_id), None)
        if fight is None:
            raise WclError(f"报告 {code} 中没有 fight {fight_id}")
        from ...bosses.base import WclSlice
        from .events import cached_events

        slices = tuple(
            WclSlice(view, "All", hostility=view, resources=True) for view in ("Friendlies", "Enemies")
        )
        pulls = pull_numbers(rep.get("fights") or [])
        return cached_events(
            self,
            self.host,
            code,
            fight,
            slices,
            progress=progress,
            revision=rep.get("revision"),
            force_refresh=force_refresh,
            convert_events=lambda raw: convert(
                rep, fight, raw, pulls.get(fight_id, 0), source=f"wcl:{code}#{fight_id}"
            ),
        )
