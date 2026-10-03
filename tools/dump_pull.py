# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""Debug helper: list the encounters of a combat log, or dump spell / unit statistics of one pull.

    uv run python tools/dump_pull.py LOG                     # list encounters
    uv run python tools/dump_pull.py LOG --seq 31            # stats of encounter #31 (index order)
    uv run python tools/dump_pull.py LOG --seq 31 --spell 1283485 --npc 268042   # raw events
"""

from __future__ import annotations

import argparse
import collections
import sys
import time

from wcl_replay.core.models import ActorKind
from wcl_replay.core.tracks import Tracks
from wcl_replay.sources.local_log import index_log, parse_encounter


def fmt_t(ms: int) -> str:
    return f"{ms // 60000}:{ms // 1000 % 60:02d}.{ms % 1000:03d}"


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("log")
    ap.add_argument("--seq", type=int, help="encounter number in the index (1-based)")
    ap.add_argument("--spell", type=int, action="append", default=[], help="print events of this spell id")
    ap.add_argument("--npc", type=int, action="append", default=[], help="print events involving npc id")
    ap.add_argument("--window", type=float, nargs=2, metavar=("FROM_S", "TO_S"),
                    help="print every NPC-sourced event in this time window (seconds)")
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--top", type=int, default=80)
    args = ap.parse_args()

    t0 = time.perf_counter()
    entries = index_log(args.log)
    print(f"indexed {len(entries)} encounters in {time.perf_counter() - t0:.1f}s")
    if args.seq is None:
        for e in entries:
            res = "kill" if e.kill else "wipe"
            print(
                f"#{e.seq:3d} pull {e.pull_number:3d} {e.encounter_id} {e.name} "
                f"{e.difficulty_label} {e.start_ts} {e.duration_ms / 1000:.1f}s {res}"
            )
        return

    entry = entries[args.seq - 1]
    t0 = time.perf_counter()
    data = parse_encounter(args.log, entry)
    tracks = Tracks(data)
    print(
        f"parsed {entry.name} pull {entry.pull_number}: {len(data.events)} events, "
        f"{sum(len(s) for s in data.samples.values())} samples in {time.perf_counter() - t0:.1f}s; map {data.map_info}"
    )
    A = data.actors

    if args.window:
        lo, hi = (int(v * 1000) for v in args.window)
        for e in data.events:
            if not lo <= e.t <= hi or e.src < 0 or A[e.src].kind is not ActorKind.NPC:
                continue
            if e.type in ("SPELL_ABSORBED", "SPELL_ABSORBED_SUPPORT", "SPELL_DAMAGE_SUPPORT"):
                continue
            s = A[e.src].name
            d = A[e.dst].name if e.dst >= 0 else "-"
            print(f"{fmt_t(e.t)} {e.type} {s} -> {d} {e.spell_id} {e.spell_name} amt={e.amount} x={e.extra}")
        return

    if args.spell or args.npc:
        npcs = set(args.npc)
        spells = set(args.spell)
        shown = 0
        for e in data.events:
            hit = e.spell_id in spells or any(
                0 <= a and A[a].npc_id in npcs for a in (e.src, e.dst)
            )
            if not hit:
                continue
            s = A[e.src].name if e.src >= 0 else "-"
            d = A[e.dst].name if e.dst >= 0 else "-"
            pos = ""
            for a in (e.src, e.dst):
                if a >= 0 and A[a].npc_id in npcs and tracks.has(a):
                    p = tracks.pose(a, e.t)
                    pos += f" [{A[a].guid[-6:]} @{p.x:.1f},{p.y:.1f} f{p.facing:.2f}]"
            print(f"{fmt_t(e.t)} {e.type} {s} -> {d} {e.spell_id} {e.spell_name} amt={e.amount} x={e.extra}{pos}")
            shown += 1
            if shown >= args.limit:
                break
        return

    print("=== hostile/neutral NPCs")
    npc_counts = collections.Counter()
    for a in A.values():
        if a.kind is ActorKind.NPC:
            npc_counts[(a.npc_id, a.name)] += 1
    for (nid, name), cnt in npc_counts.most_common():
        units = [a for a in A.values() if a.npc_id == nid]
        samples = [len(data.samples.get(a.id, [])) for a in units]
        print(f"  {nid} {name}: {cnt} units, samples min/max {min(samples)}/{max(samples)}")

    print("=== NPC spells (by event type)")
    c = collections.Counter()
    for e in data.events:
        if e.src >= 0 and A[e.src].kind is ActorKind.NPC and e.spell_id:
            c[(A[e.src].name, e.type, e.spell_id, e.spell_name)] += 1
    for k, v in c.most_common(args.top):
        print(f"  {v:6d} {k}")

    print("=== players")
    for p in data.players():
        tr = tracks.track(p.id)
        print(f"  {p.short_name} spec={p.spec_id} {p.class_name} samples={len(tr) if tr else 0}")
    print("bounds", tracks.bounds([p.id for p in data.players()]))


if __name__ == "__main__":
    main()
