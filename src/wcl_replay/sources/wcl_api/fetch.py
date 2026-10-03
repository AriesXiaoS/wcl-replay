# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Download one fight with the slices its boss module declares, then build FightData."""

from __future__ import annotations

from collections.abc import Callable

from ...bosses import module_for
from ...core.models import FightData
from .client import WclClient, WclError
from .convert import convert, pull_numbers
from .events import cached_events, download
from .urls import parse_report_url
from .urls import report_host as report_host

Progress = Callable[[float, str], None]
_download = download

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
    try:
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
        raw = cached_events(client, client.host, code, fight, slices, module.name, progress)
        pulls = pull_numbers(report.get("fights") or [])
        return convert(report, fight, raw, pulls.get(fight_id, 0), source=f"wcl:{code}#{fight_id}")
    finally:
        client.close()
