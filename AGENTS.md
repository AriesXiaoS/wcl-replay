# wcl-replay

魔兽世界战斗日志与 Warcraft Logs 回放工具。桌面界面用 PySide6，分析逻辑不依赖 Qt。Python 3.13，依赖与启动一律用 uv。

## 命令

在仓库根目录执行。不要手写激活虚拟环境的步骤。

```powershell
uv sync
uv run wcl-replay
uv run wcl-replay path\to\WoWCombatLog.txt
uv run pytest
uv run ruff check .
uv run ruff format .
uv run --no-dev --group package python tools/build_windows.py
uv run python tools/pack_windows.py
```

调试单次 pull：`uv run python tools/dump_pull.py LOG`。无界面截图：`uv run python tools/snapshot.py LOG --seq N --at 16 --out snapshots`。Windows 目录包在 `build/nuitka/wcl_replay_entry.dist/`，`--console` 会保留控制台窗口。`pack_windows.py` 打出带版本号的 zip，放在 `build/`，下一次构建删 `build/nuitka/` 时不会把它清掉。

## 结构

源码在 `src/wcl_replay`。数据从两种来源汇成同一份 `FightData`，再交给首领模块。

- `sources/local_log`：本地 `WoWCombatLog` 索引与解析。
- `sources/wcl_api`：WCL API v2（OAuth + GraphQL），结果落磁盘缓存。
- `core`：与来源无关的模型、轨迹、标记、职业颜色。
- `pipeline.analyze`：`FightData` → `Tracks` → `Analysis`。这里和 `bosses/`、`core/` 都不能 import Qt。
- `bosses`：`registry` 自动发现子包。用 `@register`，并在模块类上声明 `encounter_ids`。现有实现是 `bosses/coiled_altar`。
- `ui`：只消费 `Analysis` 给出的图元（圆、扇形、线、阶段、时间轴、日志）。新首领不要改 UI。

坐标是魔兽世界码：`x` 向北，`y` 向西。时间是该场战斗开始后的毫秒。

## 改代码时

- 新首领做成 `bosses/<name>/` 子包：常量、阶段模型、`BossModule` 子类。在包的 `__init__` 里导入模块类，保证 `discover()` 能注册它。未登记的 encounter 走通用 `BossModule`。
- 机制判断放在阶段模型里，通过 `overlays_at` / `hud_at` / `lanes` / `log` 等交给界面。图元坐标用世界坐标。
- 公共类型用 `dataclass(slots=True)`。文件开头保留 `from __future__ import annotations`。
- 界面文案用简体中文。代码注释保持与周围文件一致。
- ruff：行宽 110，目标 `py313`，规则 `E F W I UP B`，忽略 `E501`。测试在 `tests/`，共用夹具在 `tests/fixture_log.py` 与 `tests/conftest.py`。
- WCL Client ID / Secret 存在 `QSettings("wcl_replay", "wcl_replay")`。不要把凭证、战斗日志或 API 缓存写进仓库。
- 代码审查、修复过程等临时记录保存在仓库外，不放入 `docs/`，不提交到 Git；`docs/` 保留使用、扩展等长期项目文档。

## Git 提交署名

- Codex 参与编写或修改的提交，在提交说明末尾空一行后添加 `Co-authored-by: Codex <codex@openai.com>`，让 GitHub 识别共同作者。未参与的提交不添加此署名，保留实际提交作者。
