# Copyright (c) 2026 伐竹取道 (AriesXiao)
# SPDX-License-Identifier: PolyForm-Noncommercial-1.0.0

"""The Coiled Altar (盘卷祭坛, 12.1 Mythic): ids verified against a Mythic log, plus tunable geometry.

Radii / angles / speeds are not in the combat log; they are presets that can be tuned here.
"""

ENCOUNTER_ID = 3429

# Zul'jan stands on (1158.6, 0.02) at the start of every pull: the middle of the altar.
# No guide publishes the floor size. Across the Oct 1-2 pulls, coalesced venom stops
# about 46 yards out on all four sides, and living players camp the south lip to about
# 50 before a gap (a lone cell at 60 is off the floor). That is a square near 90 yards
# on a side, not a rectangle. Widow's Kiss is 50 yards, the axe eruption, not this floor.
# The view is the square plus a small lip.
PLATFORM_CENTER = (1158.6, 0.0)
PLATFORM_SIDE = 90.0
PLATFORM_PAD = 8.0
_PLATFORM_HALF = PLATFORM_SIDE / 2
_VIEW_HALF = _PLATFORM_HALF + PLATFORM_PAD
PLATFORM_X = (PLATFORM_CENTER[0] - _PLATFORM_HALF, PLATFORM_CENTER[0] + _PLATFORM_HALF)
PLATFORM_Y = (PLATFORM_CENTER[1] - _PLATFORM_HALF, PLATFORM_CENTER[1] + _PLATFORM_HALF)
ALTAR_X = (PLATFORM_CENTER[0] - _VIEW_HALF, PLATFORM_CENTER[0] + _VIEW_HALF)
ALTAR_Y = (PLATFORM_CENTER[1] - _VIEW_HALF, PLATFORM_CENTER[1] + _VIEW_HALF)

# -- units
NPC_ZULJAN = 257911  # 祖尔加 (P1 boss)
NPC_MALACRASS = 259854  # 妖术领主玛拉卡斯 (P2 boss)
NPC_GREEN = 268042  # 凝结的毒液追踪者: green globule on the floor
NPC_PURPLE = 271982  # 烈毒变异体: purple mutation on the floor
NPC_AXE = 260474  # 碎斧
NPC_GHOST = 261218  # 恐惧具象
NPC_SOULCOILER = 261521  # 怨毒盘魂者

# -- P1
GREEN_CARRY = 1282419  # 烈性毒液: player is carrying a green globule
PURPLE_CARRY = 1310498  # 诱变毒液: player is carrying a purple mutation
GREEN_PLACE = 1282403  # green globule cast; the advanced block is its floor position
PURPLE_PLACE = 1309742  # purple globule cast; the advanced block is its floor position
PURPLE_PLACE_EXTRA = 1310554  # second cast of the same purple globule, same spot, not a new one
GREEN_SUMMON = 1299781  # wave summon; the line itself has no coordinates
PURPLE_SUMMON = 1310544
DELUGE = 1299960  # 剧毒洪流: a new wave of globules follows
SEVER = 1299684  # 撕裂: tank hit, pops the globules in front of the boss
SEVER_DEBUFF = 1301690
RUPTURE = 1299838  # 毒液爆裂: raid-wide stack per popped globule
GUILLOTINE = 1283485  # 处斩: soak mark on a player
GUILLOTINE_HIT = 1283594
GRIM_GUILLOTINE = 1299266  # 冷酷处斩: P3 soak mark
GRIM_GUILLOTINE_HIT = 1299301
FANG = 1282287  # 毒牙
AXES = 1283840  # 碎斧 summons

# -- P3
DEFILEMENT = 1298381  # 盘卷祭坛亵渎: Zul'jan 回来，阶段三开始

# -- P2
DREADMARCH = 1285643  # 恐惧行军
POSSESSED = 1297445  # 恐惧行军: the cast, and again when a ghost catches its target
GHOST_SUMMON = 1285844
FIXATE = 1285911  # 令人不安的凝视: ghost -> its player
SOUL_SEVER = 1286620  # 灵魂撕裂: tank buster that also severs ghosts
SOUL_SEVER_GHOST = 1307959
BLIGHTED_SEVER = 1307292  # 凋零撕裂: P3 frontal, pops orbs the same way Sever does
GLOOMBOMB_CAST = 1310882
GLOOMBOMB = 1310881  # 幽暗炸弹 debuff (5 s)
GLOOMBOMB_HIT = 1310883
ENTOMBED = 1286837  # 墓缚
RESONANCE_MARK = 1310732  # 恶毒共鸣触发（光环隐藏，战斗日志里通常不挂在玩家身上）
RESONANCE = 1310744  # 恶毒共鸣: debuff that lands on the two fixated players
WAIL = 1286399  # 恐惧哀嚎 cast (and the fear when it goes off)
WAIL_INTERRUPTED = 1308011
TONGUES = 1714  # 语言诅咒: cast time × 1.3 while the aura is up
SOUL_SHIELD = 1309105  # 灵魂之盾 on soulcoilers (2 stacks)
SOULCOILER_SUMMON = 1286498

# -- tunable geometry / timing presets
SEVER_CONE_DEG = 60.0  # SpellTargetRestrictions.ConeDegrees for 1299684 / 1286620 / 1307292
SEVER_RANGE = 35.0  # spell 1299684, SpellRadius 21
SOUL_SEVER_RANGE = 45.0  # spell 1286620, SpellRadius 11
BLIGHTED_SEVER_RANGE = 45.0  # spell 1307292, SpellRadius 11
SEVER_PREVIEW_MS = 6000
GUILLOTINE_RADIUS = 9.0  # soak around the marked player, journal radius
GLOOMBOMB_RADIUS = 15.0  # spell 1310883, Wowhead effect radius
GHOST_SPEED = 3.0  # yards / second; not in the log, overridable from the UI slider
GHOST_FACE_DEG = 22.5  # half-angle: ghost stops while the player faces it within ±this many degrees
GHOST_STEP_MS = 100
CARRY_MS = 5000
# Spell 1286399's record is 15s (Wowhead). The Sep 15 2026 hotfix sets Heroic to 12s and Mythic to 10s.
WAIL_CAST_MS = {15: 12_000, 16: 10_000}
WAIL_CAST_MS_DEFAULT = 15_000
TONGUES_CAST_MULT = 1.3
PICKUP_MAX_DIST = 15.0

# -- colors
C_GREEN = "#5fd35f"
C_PURPLE = "#b06cff"
C_SEVER = "#e8664d"
C_GUILLOTINE = "#e8b84d"
C_DELUGE = "#4db8e8"
C_GHOST = "#d9d6f0"
C_GHOST_FILL = "#3e3b52"  # icon disc; dark so a priest's white letter does not match the fill
C_GLOOMBOMB = "#7f86ff"
C_KICK = "#3fd0c8"
C_PHASE = "#b58cff"
C_PLATFORM = "#e4d2b0"
PATH_ALPHA = 0.2  # carry and ghost routes: pale enough that a whole phase of them stays in the background
PATH_WIDTH = 1.1
C_RESONANCE = "#ff5fa2"
