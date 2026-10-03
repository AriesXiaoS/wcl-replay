# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Time the WCL queries this replay actually needs for one pull.

Reads WCL_ID and WCL_SECRET from .env in the repo root. The values are never printed.

    uv run python tools/probe_wcl_budget.py
    uv run python tools/probe_wcl_budget.py --url https://cn.warcraftlogs.com/reports/CODE?fight=3
"""

from __future__ import annotations

import argparse
import math
import statistics
import sys
import time
from pathlib import Path

from wcl_replay.bosses.base import WclSlice
from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.sources.wcl_api.client import WclClient, WclError
from wcl_replay.sources.wcl_api.events import download
from wcl_replay.sources.wcl_api.urls import parse_report_url

ROOT = Path(__file__).resolve().parents[1]
PAGE = 10000
BOSS_NPCS = {C.NPC_ZULJAN, C.NPC_MALACRASS}
ADD_NPCS = {
    C.NPC_GREEN: "绿球",
    C.NPC_PURPLE: "紫球",
    C.NPC_GHOST: "魂",
    C.NPC_SOULCOILER: "盘魂者",
    C.NPC_AXE: "碎斧",
}
AURA_IDS = (
    C.GREEN_CARRY,
    C.PURPLE_CARRY,
    C.GUILLOTINE,
    C.GRIM_GUILLOTINE,
    C.SEVER_DEBUFF,
    C.POSSESSED,
    C.FIXATE,
    C.SOUL_SEVER_GHOST,
    C.GLOOMBOMB,
    C.RESONANCE,
    C.RESONANCE_MARK,
    C.WAIL,
    C.TONGUES,
    C.SOUL_SHIELD,
    C.ENTOMBED,
)

META_QUERY = """
query($code: String!) {
  rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
  reportData {
    report(code: $code) {
      title
      startTime
      fights {
        id encounterID name difficulty kill startTime endTime
        enemyNPCs { id gameID }
        friendlyPlayers
      }
      masterData(translate: false) {
        actors { id name type gameID }
      }
    }
  }
}
"""

BUNDLE_QUERY = """
query($code: String!, $fight: [Int]!, $start: Float!, $end: Float!, $auras: String!) {
  rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
  reportData {
    report(code: $code) {
      casts: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Casts,
        hostilityType: Enemies, includeResources: true, limit: 10000, translate: false) {
        data nextPageTimestamp
      }
      debuffs: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Debuffs,
        filterExpression: $auras, limit: 10000, translate: false) {
        data nextPageTimestamp
      }
      buffs: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Buffs,
        filterExpression: $auras, limit: 10000, translate: false) {
        data nextPageTimestamp
      }
      deaths: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Deaths,
        limit: 10000, translate: false) {
        data nextPageTimestamp
      }
      summons: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Summons,
        limit: 10000, translate: false) {
        data nextPageTimestamp
      }
      interrupts: events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: Interrupts,
        limit: 10000, translate: false) {
        data nextPageTimestamp
      }
    }
  }
}
"""

SLICE_QUERY = """
query($code: String!, $fight: [Int]!, $start: Float!, $end: Float!, $kind: EventDataType!,
      $hostility: HostilityType, $resources: Boolean!, $filter: String) {
  rateLimitData { limitPerHour pointsSpentThisHour pointsResetIn }
  reportData {
    report(code: $code) {
      events(fightIDs: $fight, startTime: $start, endTime: $end, dataType: $kind,
        hostilityType: $hostility, includeResources: $resources, filterExpression: $filter,
        limit: 10000, translate: false) {
        data nextPageTimestamp
      }
    }
  }
}
"""


class Budget:
    def __init__(self, limit: int, spent: float, cap: float):
        if not math.isfinite(cap) or cap <= 0:
            raise ValueError("本次预算上限必须为有限正数")
        self.limit = limit
        self.spent = spent
        self.start = spent
        self.cap = cap
        self.calls = 0
        self.initialized = limit > 0
        self.total = 0.0
        self.reset_at: float | None = None

    @property
    def used(self) -> float:
        return self.total

    def over(self) -> bool:
        return self.used >= self.cap


def load_env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def aura_filter() -> str:
    return " or ".join(f"ability.id = {spell_id}" for spell_id in AURA_IDS)


def note_points(budget: Budget, block: dict | None) -> None:
    if not block:
        return
    spent = float(block["pointsSpentThisHour"])
    now = time.monotonic()
    if not budget.initialized:
        budget.start = spent
        budget.initialized = True
    elif spent < budget.spent or (budget.reset_at is not None and now >= budget.reset_at):
        budget.total += max(0.0, spent)
    else:
        budget.total += max(0.0, spent - budget.spent)
    budget.limit = int(block["limitPerHour"])
    budget.spent = spent
    if "pointsResetIn" in block:
        budget.reset_at = now + float(block["pointsResetIn"])


def owner_of(ev: dict) -> tuple[int, int] | None:
    if "x" not in ev or "y" not in ev:
        return None
    who = ev.get("resourceActor")
    if who not in (1, 2):
        who = 2 if ev.get("type") in ("damage", "heal", "absorbed") else 1
    if who == 1 and ev.get("sourceID") is not None:
        return int(ev["sourceID"]), int(ev.get("sourceInstance") or 0)
    if who == 2 and ev.get("targetID") is not None:
        return int(ev["targetID"]), int(ev.get("targetInstance") or 0)
    return None


def gaps(times: list[int]) -> list[int]:
    ordered = sorted(set(times))
    return [b - a for a, b in zip(ordered, ordered[1:], strict=False) if b > a]


def pct(values: list[int], p: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * p)))
    return ordered[index]


class Probe:
    def __init__(self, client: WclClient, budget: Budget):
        self.client = client
        self.budget = budget
        self.rows: list[tuple[str, int, float, float]] = []

    def call(self, label: str, query: str, variables: dict) -> dict:
        if self.budget.over():
            raise WclError(f"已用点数达到本次上限 {self.budget.cap:.0f}，停止后续请求")
        before = self.budget.used
        started = time.perf_counter()
        data = self.client.query(query, variables)
        elapsed = time.perf_counter() - started
        note_points(self.budget, data.get("rateLimitData"))
        self.budget.calls += 1
        delta = self.budget.used - before
        self.rows.append((label, 1, elapsed, delta))
        print(f"  {label:<22} {elapsed:6.2f}s   {delta:7.1f} 点   累计 {self.budget.used:.1f}", flush=True)
        return data


def fight_of(report: dict, fight_id: int) -> dict:
    fight = next((f for f in report.get("fights") or [] if int(f["id"]) == fight_id), None)
    if fight is None:
        ids = [int(f["id"]) for f in report.get("fights") or []]
        raise WclError(f"报告里没有 fight {fight_id}。现有 id：{ids}")
    return fight


def coverage(
    events: list[dict], actors: dict[int, dict], player_ids: set[int]
) -> dict[tuple[int, int], list[dict]]:
    samples: dict[tuple[int, int], list[dict]] = {}
    for ev in events:
        who = owner_of(ev)
        if who is None:
            continue
        samples.setdefault(who, []).append(ev)
    return samples


def summarize_players(samples: dict[tuple[int, int], list[dict]], player_ids: set[int]) -> None:
    medians: list[int] = []
    facing = 0
    positioned = 0
    total = 0
    for pid in sorted(player_ids):
        evs = samples.get((pid, 0), [])
        if not evs:
            evs = [ev for (aid, _inst), group in samples.items() if aid == pid for ev in group]
        if not evs:
            continue
        positioned += 1
        total += len(evs)
        facing += sum(1 for ev in evs if "facing" in ev and ev["facing"] is not None)
        gap = gaps([int(ev["timestamp"]) for ev in evs])
        mid = pct(gap, 0.5)
        if mid is not None:
            medians.append(mid)
    missing = len(player_ids) - positioned
    print(
        f"玩家 {len(player_ids)} 人，有坐标 {positioned}，没有坐标 {missing}，坐标点 {total}，带朝向 {facing}"
    )
    if medians:
        print(
            f"每人相邻坐标间隔：中位 {pct(medians, 0.5)} ms，最疏的那位中位 {max(medians)} ms，"
            f"全体中位的中位 {statistics.median(medians):.0f} ms"
        )


def summarize_npcs(samples: dict[tuple[int, int], list[dict]], actors: dict[int, dict]) -> None:
    by_game: dict[int, set[tuple[int, int]]] = {}
    for key in samples:
        game = actors.get(key[0], {}).get("gameID")
        if game:
            by_game.setdefault(int(game), set()).add(key)
    for game, label in ADD_NPCS.items():
        keys = by_game.get(game, set())
        points = sum(len(samples[key]) for key in keys)
        print(f"{label}（npc {game}）有坐标的实例 {len(keys)}，坐标点 {points}")
    for game, name in ((C.NPC_ZULJAN, "祖尔加"), (C.NPC_MALACRASS, "玛拉卡斯")):
        keys = by_game.get(game, set())
        points = sum(len(samples[key]) for key in keys)
        print(f"{name} 有坐标的实例 {len(keys)}，坐标点 {points}")


def count_casts(events: list[dict], actors: dict[int, dict]) -> None:
    buckets = {"首领": 0, "盘魂者": 0, "其他敌方": 0}
    starts = {"首领": 0, "盘魂者": 0}
    for ev in events:
        if ev.get("type") not in ("cast", "begincast"):
            continue
        game = (
            int((actors.get(int(ev["sourceID"])) or {}).get("gameID") or 0)
            if ev.get("sourceID") is not None
            else 0
        )
        if game in BOSS_NPCS:
            slot = "首领"
        elif game == C.NPC_SOULCOILER:
            slot = "盘魂者"
        else:
            slot = "其他敌方"
        buckets[slot] += 1
        if ev.get("type") == "begincast" and slot in starts:
            starts[slot] += 1
    print(
        f"敌方施法事件 {sum(buckets.values())}：首领 {buckets['首领']}（其中开始读条 {starts['首领']}），"
        f"盘魂者 {buckets['盘魂者']}（开始读条 {starts['盘魂者']}），其他 {buckets['其他敌方']}"
    )


def count_fixates(events: list[dict]) -> int:
    return sum(1 for ev in events if ev.get("abilityGameID") == C.FIXATE and ev.get("type") == "applydebuff")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--url",
        default="https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=3",
    )
    ap.add_argument("--max-points", type=float, default=1200.0)
    args = ap.parse_args()
    if not math.isfinite(args.max_points) or args.max_points <= 0:
        ap.error("--max-points 必须为有限正数")
    code, fight_id = parse_report_url(args.url)
    if fight_id is None:
        raise SystemExit("链接里没有 fight id")
    host = args.url.split("/")[2] if "://" in args.url else "cn.warcraftlogs.com"

    env = load_env(ROOT / ".env")
    client_id = env.get("WCL_ID", "")
    client_secret = env.get("WCL_SECRET", "")
    if not client_id or not client_secret:
        raise SystemExit("仓库根目录 .env 里需要 WCL_ID 和 WCL_SECRET")

    client = WclClient(client_id, client_secret, host=host, timeout=120.0)
    try:
        run_probe(client, code, fight_id, host, args.max_points)
    finally:
        client.close()


def run_probe(client: WclClient, code: str, fight_id: int, host: str, max_points: float) -> None:
    print(f"报告 {code}  fight {fight_id}  主机 {host}", flush=True)
    probe = Probe(client, Budget(0, 0.0, max_points))

    meta = probe.call("报告元数据", META_QUERY, {"code": code})
    report = meta["reportData"]["report"]
    if report is None:
        raise SystemExit("报告不存在，或这把密钥访问不到（私有报告需要用户授权，不能只用 Client ID）")
    fight = fight_of(report, fight_id)
    actors = {int(a["id"]): a for a in (report.get("masterData") or {}).get("actors") or []}
    player_ids = {int(i) for i in fight.get("friendlyPlayers") or []}
    duration = (fight["endTime"] - fight["startTime"]) / 1000
    print(
        f"{report.get('title')}  |  {fight.get('name')}  难度 {fight.get('difficulty')}  "
        f"{'击杀' if fight.get('kill') else '灭团'}  {duration:.0f}s  玩家 {len(player_ids)}",
        flush=True,
    )
    print(
        f"额度 {probe.budget.limit}/小时，首个响应基准 {probe.budget.start:.1f}（不含首请求成本）", flush=True
    )

    expression = aura_filter()
    print("一次请求拿敌方施法、机制光环、死亡、召唤、打断", flush=True)
    bundled = probe.call(
        "打包查询",
        BUNDLE_QUERY,
        {
            "code": code,
            "fight": [fight_id],
            "start": float(fight["startTime"]),
            "end": float(fight["endTime"]),
            "auras": expression,
        },
    )
    fields = bundled["reportData"]["report"]
    collected: dict[str, list[dict]] = {}
    for name in ("casts", "debuffs", "buffs", "deaths", "summons", "interrupts"):
        kind = {
            "casts": "Casts",
            "debuffs": "Debuffs",
            "buffs": "Buffs",
            "deaths": "Deaths",
            "summons": "Summons",
            "interrupts": "Interrupts",
        }[name]
        collected[name] = paginate_with_code(
            probe,
            code,
            name,
            kind,
            fight,
            hostility="Enemies" if name == "casts" else None,
            resources=name == "casts",
            expression=expression if name in ("debuffs", "buffs") else None,
            first_page=fields[name],
        )
        print(f"    {name:<12} {len(collected[name]):6d} 条", flush=True)

    print("玩家走位：友方受到的伤害（坐标在目标身上）", flush=True)
    player_events = paginate_with_code(
        probe, code, "玩家受伤", "DamageTaken", fight, hostility="Friendlies", resources=True
    )
    print(f"    玩家受伤 {len(player_events)} 条", flush=True)

    positioned = [ev for ev in player_events if owner_of(ev)]
    print(f"    其中带坐标 {len(positioned)}，带 facing 字段 {sum(1 for ev in positioned if 'facing' in ev)}")
    if positioned:
        print(f"    坐标事件字段：{', '.join(sorted(positioned[0]))}", flush=True)

    samples = coverage(player_events + collected["casts"], actors, player_ids)
    print("—— 只靠「敌方施法 + 玩家受伤」时的覆盖 ——", flush=True)
    summarize_players(samples, player_ids)
    summarize_npcs(samples, actors)
    count_casts(collected["casts"], actors)
    auras = collected["debuffs"] + collected["buffs"]
    print(
        f"机制光环 {len(auras)}，其中魂盯人（凝视施加）{count_fixates(auras)}，"
        f"死亡 {len(collected['deaths'])}，召唤 {len(collected['summons'])}，打断 {len(collected['interrupts'])}",
        flush=True,
    )

    # source.id / target.id 过滤这次会返回 0 条；球的坐标在它们自己的施法上，用名字过滤。
    print("补球的坐标：按单位名字过滤它自己的施法", flush=True)
    for label, npc_name in (("绿球", "凝结的毒液追踪者"), ("紫球", "烈毒变异体")):
        events = paginate_with_code(
            probe,
            code,
            label,
            "Casts",
            fight,
            resources=True,
            expression=f'source.name = "{npc_name}" and type = "cast"',
        )
        hit = [ev for ev in events if owner_of(ev)]
        instances = {(ev.get("sourceID"), ev.get("sourceInstance")) for ev in hit}
        print(f"    {label} {len(events)} 条施法，带坐标 {len(hit)}，实例 {len(instances)}", flush=True)
        for ev in hit:
            who = owner_of(ev)
            if who is not None:
                samples.setdefault(who, []).append(ev)

    print("—— 补完小怪之后 ——", flush=True)
    summarize_npcs(samples, actors)
    summarize_players(samples, player_ids)

    elapsed = sum(row[2] for row in probe.rows)
    print(
        f"合计 {probe.budget.calls} 次请求，墙钟 {elapsed:.1f}s，本次消耗 {probe.budget.used:.1f} 点"
        f"（上限 {probe.budget.limit} / 小时）",
        flush=True,
    )


def paginate_with_code(
    probe: Probe,
    code: str,
    label: str,
    kind: str,
    fight: dict,
    *,
    hostility: str | None = None,
    resources: bool = False,
    expression: str | None = None,
    first_page: dict | None = None,
) -> list[dict]:
    """Use the production paginator, with quota accounting at the transport boundary."""

    class Metered:
        pages = 0

        def query(self, _query: str, variables: dict) -> dict:
            self.pages += 1
            if self.pages == 1 and first_page is not None:
                return {"reportData": {"report": {"events": first_page}}}
            return probe.call(f"{label}#{self.pages}", SLICE_QUERY, variables)

    return download(
        Metered(),
        code,
        fight,
        (WclSlice(label, kind, hostility=hostility or "", resources=resources, filter=expression or ""),),
    )


if __name__ == "__main__":
    try:
        main()
    except WclError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        raise SystemExit(1) from exc
