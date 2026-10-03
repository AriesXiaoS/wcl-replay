# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Compare one WCL fight with the newest local combat log. Prints a short report. Not a unit test."""

from __future__ import annotations

import math
import statistics
import sys
from collections import Counter
from pathlib import Path

from wcl_replay.bosses.coiled_altar import constants as C
from wcl_replay.core.models import FightData
from wcl_replay.sources.local_log import index_log, parse_encounter
from wcl_replay.sources.wcl_api.fetch import fetch_fight

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = Path(r"G:\World of Warcraft\_retail_\Logs")
URL = "https://cn.warcraftlogs.com/reports/ya73XMW2TkvcnRQV?fight=3"
CASTS = (
    C.DELUGE,
    C.SEVER,
    C.FANG,
    C.DREADMARCH,
    C.SOUL_SEVER,
    C.GLOOMBOMB_CAST,
    C.WAIL,
    C.DEFILEMENT,
    C.BLIGHTED_SEVER,
)


def load_env() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def newest_log() -> Path:
    files = sorted(LOG_DIR.glob("WoWCombatLog*.txt"), key=lambda p: p.stat().st_mtime)
    if not files:
        raise SystemExit(f"没有找到日志：{LOG_DIR}")
    return files[-1]


def casts(data: FightData, spell_id: int) -> list[int]:
    return sorted(e.t for e in data.events if e.spell_id == spell_id and e.type == "SPELL_CAST_SUCCESS")


def starts(data: FightData, spell_id: int) -> list[int]:
    return sorted(e.t for e in data.events if e.spell_id == spell_id and e.type == "SPELL_CAST_START")


def aura_applies(data: FightData, spell_id: int) -> int:
    return sum(1 for e in data.events if e.spell_id == spell_id and e.type == "SPELL_AURA_APPLIED")


def npc_count(data: FightData, npc_id: int) -> int:
    return sum(1 for a in data.actors_by_npc(npc_id) if data.samples.get(a.id))


def player_by_name(data: FightData) -> dict[str, int]:
    return {a.short_name: a.id for a in data.players()}


def pose_at(data: FightData, aid: int, t: int, window: int = 250) -> tuple[float, float, float] | None:
    samples = data.samples.get(aid) or []
    if not samples:
        return None
    best = min(samples, key=lambda s: abs(s.t - t))
    if abs(best.t - t) > window:
        return None
    return best.x, best.y, best.facing


def angle_diff(a: float, b: float) -> float:
    d = (a - b + math.pi) % (2 * math.pi) - math.pi
    return abs(d)


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    env = load_env()
    path = newest_log()
    print(f"本地日志 {path.name}  {path.stat().st_size / 1e9:.2f} GB", flush=True)
    entries = index_log(str(path))
    print(
        f"索引到 {len(entries)} 场，最后一场 {entries[-1].name} {entries[-1].difficulty_label} "
        f"{entries[-1].duration_ms} ms {'击杀' if entries[-1].kill else '灭团'}",
        flush=True,
    )
    print("拉取 WCL…", flush=True)
    wcl = fetch_fight(
        env["WCL_ID"], env["WCL_SECRET"], "cn.warcraftlogs.com", URL, lambda _f, m: print(" ", m, flush=True)
    )
    print(
        f"WCL {wcl.fight.name} 难度 {wcl.fight.difficulty} {wcl.fight.duration_ms} ms "
        f"{'击杀' if wcl.fight.kill else '灭团'} 事件 {len(wcl.events)}",
        flush=True,
    )
    altar = [e for e in entries if e.encounter_id == C.ENCOUNTER_ID]
    if not altar:
        raise SystemExit("本地日志里没有盘卷祭坛")
    local_entry = min(altar, key=lambda e: (abs(e.duration_ms - wcl.fight.duration_ms), -e.start_offset))
    print(
        f"对照本地 #{local_entry.pull_number} {local_entry.start_ts} {local_entry.duration_ms} ms "
        f"差值 {local_entry.duration_ms - wcl.fight.duration_ms} ms",
        flush=True,
    )
    local = parse_encounter(str(path), local_entry)
    offset_samples = []
    print("施法成功条数 / 时间差（本地 - WCL，已对齐第一对）")
    anchor = None
    for spell_id in CASTS:
        lt, wt = casts(local, spell_id), casts(wcl, spell_id)
        if lt and wt and anchor is None:
            anchor = lt[0] - wt[0]
        deltas = [lt[i] - wt[i] - (anchor or 0) for i in range(min(len(lt), len(wt)))]
        offset_samples.extend(deltas)
        mid = statistics.median(deltas) if deltas else None
        print(f"  {spell_id} 本地 {len(lt):3d}  WCL {len(wt):3d}  对齐后中位差 {mid}")
        ls, ws = starts(local, spell_id), starts(wcl, spell_id)
        print(f"         开始读条 本地 {len(ls):3d}  WCL {len(ws):3d}")
    if offset_samples:
        print(f"全部成对施法，对齐后中位差 {statistics.median(offset_samples):.0f} ms，锚点 {anchor} ms")

    print(
        f"凝视施加 本地 {aura_applies(local, C.FIXATE)}  WCL {aura_applies(wcl, C.FIXATE)}  "
        f"带球绿 {aura_applies(local, C.GREEN_CARRY)}/{aura_applies(wcl, C.GREEN_CARRY)}  "
        f"紫 {aura_applies(local, C.PURPLE_CARRY)}/{aura_applies(wcl, C.PURPLE_CARRY)}"
    )
    for npc, label in (
        (C.NPC_GREEN, "绿球"),
        (C.NPC_PURPLE, "紫球"),
        (C.NPC_GHOST, "魂"),
        (C.NPC_SOULCOILER, "盘魂者"),
        (C.NPC_ZULJAN, "祖尔加"),
        (C.NPC_MALACRASS, "玛拉卡斯"),
    ):
        print(f"  有坐标的{label} 本地 {npc_count(local, npc)}  WCL {npc_count(wcl, npc)}")

    names = sorted(set(player_by_name(local)) & set(player_by_name(wcl)))
    print(f"同名玩家 {len(names)} / 本地 {len(local.players())} / WCL {len(wcl.players())}")
    dists: list[float] = []
    faces: list[float] = []
    missing = 0
    step = 5000
    off = anchor or 0
    for name in names:
        la, wa = player_by_name(local)[name], player_by_name(wcl)[name]
        t = 0
        while t < min(local.fight.duration_ms, wcl.fight.duration_ms):
            lp = pose_at(local, la, t)
            wp = pose_at(wcl, wa, t - off)
            if lp and wp:
                dists.append(((lp[0] - wp[0]) ** 2 + (lp[1] - wp[1]) ** 2) ** 0.5)
                faces.append(angle_diff(lp[2], wp[2]))
            else:
                missing += 1
            t += step
    if dists:
        dists.sort()
        print(
            f"玩家坐标距离 中位 {statistics.median(dists):.2f} 码，"
            f"90 分位 {dists[int(len(dists) * 0.9)]:.2f} 码，最大 {dists[-1]:.2f} 码，"
            f"对上 {len(dists)} 组，缺样本 {missing}"
        )
        faces.sort()
        print(f"朝向差 中位 {statistics.median(faces):.3f} 弧度，90 分位 {faces[int(len(faces) * 0.9)]:.3f}")

    boss_dists: list[float] = []
    for npc in (C.NPC_ZULJAN, C.NPC_MALACRASS):
        lb = local.actors_by_npc(npc)
        wb = wcl.actors_by_npc(npc)
        if not lb or not wb:
            continue
        for spell_id in CASTS:
            for i, wt in enumerate(casts(wcl, spell_id)):
                lt_list = casts(local, spell_id)
                if i >= len(lt_list):
                    break
                lp = pose_at(local, lb[0].id, lt_list[i])
                wp = pose_at(wcl, wb[0].id, wt)
                if lp and wp:
                    boss_dists.append(((lp[0] - wp[0]) ** 2 + (lp[1] - wp[1]) ** 2) ** 0.5)
    if boss_dists:
        print(
            f"首领施法瞬间坐标距离 中位 {statistics.median(boss_dists):.2f} 码，"
            f"最大 {max(boss_dists):.2f} 码，{len(boss_dists)} 次"
        )

    # Orb positions: match by nearest at spawn.
    def first_pos(data: FightData, npc: int) -> list[tuple[float, float]]:
        out = []
        for actor in data.actors_by_npc(npc):
            samples = data.samples.get(actor.id) or []
            if samples:
                out.append((samples[0].x, samples[0].y))
        return out

    for npc, label in ((C.NPC_GREEN, "绿球"), (C.NPC_PURPLE, "紫球")):
        lp, wp = first_pos(local, npc), first_pos(wcl, npc)
        gaps = []
        for x, y in lp:
            if not wp:
                break
            best_d = min(((x - wx) ** 2 + (y - wy) ** 2) ** 0.5 for wx, wy in wp)
            gaps.append(best_d)
        if gaps:
            gaps.sort()
            close = sum(1 for d in gaps if d < 3)
            print(
                f"{label} 每个本地出生点到最近 WCL 点：<3 码 {close}/{len(gaps)}，"
                f"中位 {statistics.median(gaps):.2f}，90 分位 {gaps[int(len(gaps) * 0.9)]:.2f}，最大 {gaps[-1]:.2f}"
            )
        else:
            print(f"{label} 没有出生点")
    print("事件类型", Counter(e.type for e in wcl.events).most_common(8))


if __name__ == "__main__":
    main()
