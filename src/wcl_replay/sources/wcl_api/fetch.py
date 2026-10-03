# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Download one fight with the slices its boss module declares, then build FightData."""

from __future__ import annotations

import json
from collections.abc import Callable
from urllib.parse import urlparse

from ...bosses import module_for
from ...bosses.base import WclSlice
from ...core.models import FightData
from .client import WclClient, WclError
from .convert import convert, pull_numbers
from .urls import parse_report_url

Progress = Callable[[float, str], None]

META_QUERY = """
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
      masterData(translate: false) {
        actors { id name type subType icon petOwner gameID server }
        abilities { gameID name }
      }
    }
  }
}
"""

EVENTS_QUERY = """
query($code: String!, $fight: [Int]!, $start: Float!, $end: Float!, $kind: EventDataType,
      $hostility: HostilityType, $resources: Boolean!, $filter: String) {
  reportData {
    report(code: $code) {
      events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: $kind,
             hostilityType: $hostility, includeResources: $resources, filterExpression: $filter,
             limit: 10000, translate: false) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""


def report_host(url: str) -> str | None:
    """API host named by a report link, when the link is on warcraftlogs.com."""
    parsed = urlparse(url if "://" in url else "https://" + url)
    if parsed.netloc.endswith("warcraftlogs.com"):
        return parsed.netloc
    return None


def fetch_fight(
    client_id: str,
    client_secret: str,
    host: str,
    url: str,
    progress: Progress | None = None,
) -> FightData:
    """Read one fight. ``host`` is the fallback when the link does not name a site."""
    code, fight_id = parse_report_url(url)
    if fight_id is None:
        raise WclError("请使用带 fight= 的单场战斗链接")
    site = report_host(url) or host or "cn.warcraftlogs.com"
    client = WclClient(client_id, client_secret, host=site)
    if progress:
        progress(0.02, "读取报告…")
    report = client.query(META_QUERY, {"code": code})["reportData"]["report"]
    if report is None:
        raise WclError(f"找不到报告 {code}（报告不存在或为私有）")
    fight = next((f for f in report.get("fights") or [] if int(f["id"]) == fight_id), None)
    if fight is None:
        raise WclError(f"报告 {code} 中没有 fight {fight_id}")
    module = module_for(int(fight.get("encounterID") or 0))
    slices = module.wcl_slices(report, fight)
    if progress:
        progress(0.06, f"{module.name} · 准备查询")
    raw = _download(client, code, fight, slices, module.name, progress)
    pulls = pull_numbers(report.get("fights") or [])
    return convert(report, fight, raw, pulls.get(fight_id, 0), source=f"wcl:{code}#{fight_id}")


def _download(
    client: WclClient,
    code: str,
    fight: dict,
    slices: tuple[WclSlice, ...],
    boss: str,
    progress: Progress | None,
) -> list[dict]:
    start = float(fight["startTime"])
    end = float(fight["endTime"])
    seen: set[str] = set()
    out: list[dict] = []
    total = max(1, len(slices))
    for index, sl in enumerate(slices):
        cursor: float | None = start
        pages = 0
        while cursor is not None:
            data = client.query(
                EVENTS_QUERY,
                {
                    "code": code,
                    "fight": [int(fight["id"])],
                    "start": cursor,
                    "end": end,
                    "kind": sl.data_type,
                    "hostility": sl.hostility or None,
                    "resources": sl.resources,
                    "filter": sl.filter or None,
                },
            )
            page = data["reportData"]["report"]["events"]
            batch = page.get("data") or []
            for ev in batch:
                key = json.dumps(ev, sort_keys=True, ensure_ascii=False)
                if key not in seen:
                    seen.add(key)
                    out.append(ev)
            pages += 1
            nxt = page.get("nextPageTimestamp")
            prev = cursor
            cursor = float(nxt) if nxt is not None and float(nxt) > prev else None
            if not batch:
                break
            if pages >= 40:
                raise WclError(f"{sl.label} 超过 40 页，已停止")
            if progress:
                walked = index + (1.0 if cursor is None else pages / (pages + 1))
                progress(0.06 + 0.78 * walked / total, f"{boss} · {sl.label}… {len(out)} 条")
        if progress and cursor is None:
            progress(0.06 + 0.78 * (index + 1) / total, f"{boss} · {sl.label}… {len(out)} 条")
    return out
