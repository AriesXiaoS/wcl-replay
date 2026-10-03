# 代码审查记录 · 2026-10-03

> 本文记录修复前的工作区及复现结果，保留原始问题说明。后续代码修改与回归验证见
> [修复记录](code-fixes-2026-10-03.md)。下文的原始行号和复现脚本对应审查时版本。

## 结论

项目已经有适合继续扩展的主体结构：两种来源汇入 `FightData`，分析流水线和首领模块不依赖 Qt，界面通过 `Analysis` 的图元和查询接口展示结果，首领注册与发现也有独立实现。这些边界应当保留。

当前还不能认为各模块已经充分解耦、所有主要使用流程都可靠。最需要处理的是 WCL 域名校验导致的凭证泄露风险，其次是异步任务身份和当前显示来源的管理。数据转换、缓存失效、机制推断也存在会改变回放结果的错误。本次记录 **15 项具体问题和 3 项条件性风险**；以下数量不包括纯格式问题和架构改进建议。

这是对当前工作区的审查，包含已有的未提交修改；没有修改业务代码，也没有覆盖这些修改。

## 范围与验证

- 审查 `src/` 的 49 个 Python 文件、`tools/` 的 6 个 Python 文件、`tests/` 的 22 个 Python 文件，以及项目配置、启动和打包入口。
- 环境：Windows，Python **3.13.15**；通过 uv 使用现有环境。
- 完整测试：**104 passed**。测试覆盖了子进程运行、回调回到 GUI 线程、分析对象跨进程序列化，以及多种轨迹和界面行为。
- `ruff check .`：**通过**。
- `ruff format --check .`：**未通过**，`tools/dump_pull.py` 和 `tools/snapshot.py` 需要格式化。没有执行格式化写入。
- AST 导入检查：`core/`、`bosses/`、`pipeline.py`、`workers.py` 未发现直接 Qt 导入；现有子进程测试也验证了正常分析任务不加载 Qt。
- 补充 **19 个隔离的复现案例**，使用合成日志、模拟 API 响应和独立的 QSettings 文件；包括下文两项条件性风险的反例。所有案例都观察到了说明中的实际结果。多个案例对应同一问题，不能将案例数当作独立缺陷数。
- 没有调用真实 WCL 下载接口，没有读取或输出用户 API 凭证；未执行 Nuitka 完整打包，也没有用真实战斗日志验证机制推断的准确率。

默认 uv 缓存和 pytest 临时目录在本环境中不可访问，因此实际验证采用了独立缓存及可写临时目录，未安装或升级依赖。成功的完整测试命令为：

```powershell
$reviewTemp = Join-Path (Get-Location).Path ('.ruff_cache\audit-pytest-' + [guid]::NewGuid().ToString('N'))
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync pytest -p no:cacheprovider --basetemp $reviewTemp
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff check .
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff format --check .
```

复现脚本与结果放在仓库外，可从仓库根目录运行：

```powershell
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync python 'C:\Users\AriesXiao\.codex\visualizations\2026\10\03\01a10183-8d5c-7302-b129-c20d591e366f\audit_code_review.py'
```

[复现脚本](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/audit_code_review.py) · [实际结果 JSON](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/audit-artifacts/reproductions.json)

## 具体问题

优先级含义：**P1** 应优先修复，涉及凭证安全；**P2** 影响功能可用性或分析正确性，应安排修复。下面按安全、任务状态、来源数据和机制逻辑排序。合成数据复现说明代码对该输入的实际行为，不代表已经统计到真实日志中的发生频率。

### F01 · P1 · 报告链接可把 OAuth 凭证导向非 WCL 域名

位置：[urls.py:22](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/urls.py:22)、[fetch.py:63](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/fetch.py:63)、[client.py:127](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/client.py:127)。

两处使用 `netloc.endswith("warcraftlogs.com")` 校验网址。`evilwarcraftlogs.com` 满足该条件，`report_host()` 又把这个域名提供给 `WclClient`。首次认证时，`token()` 会向该域名的 `/oauth/token` 发送包含 Client ID / Secret 的 Basic Auth 请求。

复现：`https://evilwarcraftlogs.com/reports/ABCDEFGHIJKLMNOP?fight=1` 被解析成功，选择的认证 host 是 `evilwarcraftlogs.com`。验证只进行了解析，没有访问该域名。

建议：用 `urlparse(...).hostname` 解析并规范化，再校验明确的官方 host 白名单；至少必须校验域名边界，不能直接做字符串后缀判断。链接解析、host 提取和 `WclClient` 初始化应共用一套规则，明确是否支持语言子域名及自定义端点。对恶意后缀域名、userinfo、端口、大小写和裸报告代码增加测试。

### F02 · P2 · 切换来源后，后台完成结果会覆盖当前回放

位置：[main_window.py:175](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/main_window.py:175)、[log_panel.py:528](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/log_panel.py:528)、[wcl_panel.py:233](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/wcl_panel.py:233)。

本地和 WCL 两个 Board 独立保存 `selected`，却直接写同一个 `ReplayController.session`。`_set_source()` 切换面板时没有改变后台结果的显示资格。隐藏面板仍然选中的任务完成后，会重新调用 `set_session()`，覆盖另一来源正在展示的战斗。

复现：先启动本地分析，再切换 WCL；WCL 完成后显示 `WCL`，随后本地任务完成，当前 session 变成 `LOCAL`，左侧仍是 WCL 面板。反向切换也具有相同路径。

建议：集中管理当前显示来源与选中任务。后台结果可以继续进入各自缓存，但只有符合当前来源和选择身份的完成结果才允许更新回放。覆盖两个方向的完成顺序，而不仅测试单个 Board 内的选择切换。

### F03 · P2 · 释放后重算同一轮，旧任务会冒充新任务完成

位置：[log_panel.py:519](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/log_panel.py:519)、[log_panel.py:538](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/log_panel.py:538)、[main_window.py:325](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/main_window.py:325)。

释放结果只把稳定的战斗 key 从 `running` 移除，并没有取消进程任务。再次点击同一轮时，该 key 又进入 `running`。旧任务的 `finish()` 只验证这个 key，且忽略 `_gen`，因此旧回调会被当作新任务的结果。旧错误回调也可能终止新任务的状态。

复现：启动 A → 释放 → 启动 B → A 完成 → B 完成。实际接受 A、拒绝 B，最终显示 `OLD`；预期应该拒绝 A、接受 B，显示 `NEW`。

建议：区分战斗身份和一次运行的身份，每次提交生成独立 job token；完成、失败、进度回调都核对 token。不能简单恢复索引 generation 校验，因为当前设计有意保留刷新后仍有效的任务。

### F04 · P2 · 后台队列创建失败时，任务永久处于加载状态

位置：[loader.py:161](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/loader.py:161)。

`multiprocessing.get_context()` 和两个 `ctx.Queue()` 在 `_boot()` 的 `try` 外面。临时目录权限或系统资源等原因导致队列创建抛出异常时，启动线程退出，`_fail_pending()` 不执行，待处理任务没有失败回调，后续也无法正常启动这个池。

复现：模拟 `Queue()` 抛出 `OSError`。实际 `busy() == True`、失败回调次数为 0、`_boot_error` 仍为空。

建议：把完整池初始化放入统一错误处理，清理已创建的队列与进程，将待处理任务转为失败，并定义是否允许重建进程池。另外，当前结果监听没有处理进程异常退出导致的任务丢失，应补充进程存活监测和失败传播；这部分是静态风险，未进行真实进程崩溃复现。

### F05 · P2 · “测试连接”同步联网，会阻塞界面

位置：[wcl_dialogs.py:72](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/wcl_dialogs.py:72)、[client.py:95](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/client.py:95)。

按钮的 GUI 槽函数直接执行 `WclClient(...).token(force=True)`。同步 HTTP 请求期间 Qt 事件循环不能处理输入和绘制；客户端默认网络超时为 60 秒，网络较慢时整个窗口会长时间无响应。

复现：用模拟认证客户端记录调用线程，确认 `token()` 就在 QApplication 的 GUI 线程执行，没有实际联网。

建议：把连接测试提交到后台任务，期间显示进度并禁用重复提交，成功或失败后在 GUI 线程更新提示。

### F06 · P2 · 清除 WCL 缓存后，保留的战斗行无法再次计算

位置：[wcl_panel.py:207](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/wcl_panel.py:207)、[wcl_panel.py:266](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/wcl_panel.py:266)、[main_window.py:313](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/main_window.py:313)。

清缓存会保留列表行，却删除其 session。`activate()` 对既不在缓存、也不在 `running` 的 key 只返回 `idle`，主窗口也不启动查询。因此点击该行不会恢复结果。失败行也缺少直接重试路径。

复现：WCL 查询成功 → `clear_cache()` → 点击该行，实际返回 `idle`，没有启动重算。

建议：复用已保存的 URL 重新提交任务，并按 F03 的方案生成新的运行身份；或在产品上明确改为删除列表记录。当前保留记录的行为更适合支持重算。

### F07 · P2 · 主界面的 WCL 下载路径没有使用战斗事件磁盘缓存

位置：[workers.py:49](D:/Code/魔兽世界wcllog分析/src/wcl_replay/workers.py:49)、[fetch.py:91](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/fetch.py:91)、[client.py:225](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/client.py:225)。

主窗口使用 `fetch_wcl_job()` → `fetch_fight()` → `_download()`，每次都会查询全部切片。已有 gzip 事件缓存只在另一条 `WclClient.fight_data()` 路径中读取和写入，主界面并不走那里。进程内的 Board session 缓存和 OAuth token 缓存仍然存在，但不能提供跨查询、跨重启的战斗事件复用。

复现：模拟客户端连续调用两次相同 `fetch_fight()`，第二次仍发出 5 次通用首领事件查询。盘卷祭坛目前声明 10 个切片，每个切片还可能分页。

建议：合并事件下载和缓存责任，缓存键至少包含 host、报告、fight、结束时间、缓存版本及切片/过滤策略签名。避免把旧的全量缓存无条件当成新切片缓存，以免缓存内容不足或缓存失效策略不一致。

### F08 · P2 · 同一毫秒、同一位置的血量和朝向变化被去掉

位置：[parser.py:158](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/parser.py:158)、[convert.py:190](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/convert.py:190)。

两种来源都仅依据 `t/x/y` 去重，忽略 `facing/hp/max_hp`。同一毫秒内可能有多个伤害或治疗，也可能出现不同资源快照，保留首个快照会丢掉后续状态。这会影响血条、朝向展示以及依赖这些字段的机制判断。

复现：相同时间和位置，先 100 HP、再 50 HP，同时朝向变化。两种来源最终都只有 100 HP 的样本。

建议：只删除完整字段一致的快照，或显式合并同一时间的样本并保留最终状态。WCL 切片下载并非原始事件顺序，合并规则还需处理同时间跨切片的排序，不能把来源到达顺序隐含当成游戏事件顺序。

### F09 · P2 · 同路径日志被替换成更大文件时，仍沿用旧战斗索引

位置：[index.py:275](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/index.py:275)、[log_panel.py:40](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/log_panel.py:40)。

索引只因文件变大就认定是追加，保留旧的已关闭遭遇战并从旧偏移继续扫描。文件被覆盖、轮换后重新增长，或用户把另一份更大的日志放到同一路径时，该前提不成立。UI 的缓存 key 也只有路径和字节范围，即使重新扫描，等长区段变化仍可能继续使用旧分析。

复现：先索引 encounter 111；随后将同一路径覆盖成 encounter 222 并加长文件。再次索引仍返回 111。

建议：引入日志版本/身份及已扫描前缀的指纹，只有确认前缀未改变才能增量扫描；UI 的分析缓存也应关联同一来源版本。保留真正追加场景下的性能优势。

### F10 · P2 · 普通战斗日志的伤害数值被静默解析为 0

位置：[parser.py:233](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/parser.py:233)。

伤害、治疗等数值固定从 `base + ADV_LEN` 读取，没有判断事件是否存在高级资源块。非高级日志仍可以被打开并产生事件，但是普通伤害字段的偏移不同，`amount` 会保留默认 0。README 说明高级日志是绘制站位的前提，没有说明普通日志会丢失本来存在的伤害数值。

复现：普通 `SPELL_DAMAGE` 的实际数值为 777，解析后的 `Event.amount` 为 0。

建议：依据日志版本/高级日志标志选择事件结构，数值解析与位置资源块解析分开；如果只打算支持高级日志，也应在输入边界明确提示不支持，避免给出错误的零值。

### F11 · P2 · WCL 周期治疗丢失类型语义，导致目标推断错误

位置：[convert.py:31](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/convert.py:31)、[convert.py:155](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/convert.py:155)、[targets.py:17](D:/Code/魔兽世界wcllog分析/src/wcl_replay/core/targets.py:17)。

所有 `heal` 都被映射为 `SPELL_HEAL`，转换没有读取周期标记。`Targets` 有意把直接治疗当作换目标、排除周期治疗，所以一个已经挂在另一玩家身上的 HoT 跳数会错误地改变施法者的当前目标。周期伤害也存在直接/周期类型被合并的问题，影响未来基于统一事件类型的分析复用。

复现：A 先对 B 施法，再发生 A 对 C 的 `heal` 且 `tick=True`。目标从 B 错误地变成 C。

周期治疗标记的字段语义可对照 WCL 消费者的第一手类型定义：WoWAnalyzer 的 `HealEvent` 定义了 `tick?: boolean` 表示 HoT 跳数。此处用合成响应验证转换行为，未抓取本次真实 WCL 响应。[WoWAnalyzer Events.ts](https://github.com/WoWAnalyzer/WoWAnalyzer/blob/midnight/src/parser/core/Events.ts)

建议：将周期治疗、周期伤害映射为 `SPELL_PERIODIC_HEAL` / `SPELL_PERIODIC_DAMAGE`，并增加本地日志与 WCL 对等事件的结果一致性测试。

### F12 · P2 · 同时放下同色毒液球时，放球玩家归属可能互换

位置：[p1.py:271](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p1.py:271)。

`_match_drops()` 只比较携带光环结束时间与球出现时间，时间相同的候选按列表顺序匹配，没有使用玩家位置。当多个同色携带者同时间放球时，事件遍历顺序与空间归属未必一致，`dropped_by` 会被错误赋值。

复现：玩家 1 在 x=0、玩家 2 在 x=100，均在 5000ms 放球；球以 x=100、x=0 的顺序出现。实际归属是 `[1, 2]`，空间上应为 `[2, 1]`。

建议：优先使用明确来源关系；没有来源时，在时间窗内结合玩家放球位置和颜色匹配，并尽量对同时出现的一批球做整体分配。没有足够证据时保留未知归属，避免把顺序猜测作为确定结果。

### F13 · P2 · 哀嚎存在明确施法成功事件时，仍可能判为未知

位置：[p2.py:499](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p2.py:499)。

`_wails()` 用打断、恐惧光环、下一次施法和死亡判断结局，没有处理该技能的 `SPELL_CAST_SUCCESS`。如果成功施法但没有命中恐惧光环，例如全员免疫或数据只记录了施法，则结局仍是 `unknown`，施法条也延续到推定搜索上限。

复现：1000ms 开始，11000ms 成功，没有恐惧光环。实际结束于 18000ms、结局 `unknown`；明确成功事件应在 11000ms 结束并判定施放成功。

建议：使用同施法者、同技能的成功事件关闭施法，另外统计附近的恐惧应用数量；成功施放和受恐惧人数是两个不同的结果字段。

### F14 · P2 · 护盾不处理增加层数事件

位置：[p2.py:527](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p2.py:527)、[convert.py:37](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/convert.py:37)。

护盾模型处理初始应用、减少层数和移除，却忽略 `SPELL_AURA_APPLIED_DOSE`。WCL 的 `applybuffstack` 已转换为这个类型，公共光环工具的注释也专门处理了只有层数事件、没有初始应用的情况。因此这种输入下护盾会显示为 0，后续补层也不能反映出来。

复现：护盾只收到 `APPLIED_DOSE(amount=2)`，查询结果为 0。

建议：增加与减少层数都读取 `e.amount`，完整移除归零，初始应用采用明确的默认层数策略。覆盖普通应用、仅层数起始、增层、减层和移除的完整序列。

### F15 · P2 · 文档推荐的截图命令会直接报错

位置：[snapshot.py:53](D:/Code/魔兽世界wcllog分析/tools/snapshot.py:53)。

截图脚本通过 `findChild(type(win.centralWidget())).widget(1).widget(0)` 猜测窗口内部结构。当前 central widget 是普通 QWidget，查找到的子对象不一定是 splitter；在当前窗口结构下得到 QStatusBar，调用 `.widget()` 立即产生 `AttributeError`，还没走到保存 PNG。

复现：在隔离设置下构造真实 MainWindow 并执行这条表达式，得到 `QStatusBar object has no attribute 'widget'`。

建议：给需要强制刷新的组件明确引用或稳定类型接口，避免依赖子控件索引和宽泛 QWidget 类型查找。补一个合成日志的截图命令端到端测试，验证输出 PNG 存在且非空。

## 条件性风险

这些记录描述实现边界或有明确前提的反例，需要结合真实机制/日志确认优先级，不应直接宣称所有现有战斗都受影响。

### R01 · 同一目标、同一法术、多个独立来源的光环无法正确共存

位置：[base.py:300](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/base.py:300)。

公共 `aura_intervals()` 的 key 是 `(dst, spell_id)`，虽然 Interval 保存了 `src`，身份中却没有来源。对会按来源独立存在的光环，来源 A 的移除会关闭来源 B 的区间。合成反例：A 应用 → B 应用 → A 移除，B 应继续存在却变成无光环。

当前函数文档明确是按目标归并，因此不能对所有技能统一加入 `src`，有些技能本来就是覆盖或共享层数。建议提供明确的归并策略/来源独立选项，首领模块按机制选择；新增首领时必须检查光环实例语义。未验证当前盘卷祭坛是否允许此类锁定重叠。

### R02 · “同时移除至少四个标记”的启发式可能吞掉真实清除

位置：[markers.py:55](D:/Code/魔兽世界wcllog分析/src/wcl_replay/core/markers.py:55)。

100ms 内至少四个不同标记移除被一律判为离开副本产生的通知并忽略，没有检查实际换区、遭遇战边界或实例上下文。如果玩家/插件在战斗中批量清除光柱，会满足相同条件，回放继续保留它们。

合成反例：四个标记在战斗中同时间被移除，查询结果仍有四个标记。建议结合换区/会话信息判定，或将战斗内明确移除事件视作更高可信证据。未在真实日志中确认批量清除的事件形态。

### R03 · 毒液球 WCL 切片依赖中文 NPC 名称

位置：[wcl.py:36](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/wcl.py:36)、[fetch.py:34](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/fetch.py:34)、[fetch.py:50](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/fetch.py:50)。

过滤表达式硬编码两个中文 NPC 名称，`slices()` 没有使用报告 metadata，而查询又指定 `translate:false`。当原始名称不是这两个中文字符串时，这个名称过滤切片不能匹配，地面球所需数据可能缺失。界面支持 www 域名，所以该限制应该有明确处理。

这是依据表达式的静态推断，未下载国际服报告验证缺失程度。WCL 官方也提示，名称匹配不能跨语言复用。[WCL 查询文档](https://de.warcraftlogs.com/help/pins)

建议：依据 metadata 中的稳定 gameID 识别 NPC，再用报告中的实际名称/实例身份构造过滤条件，或使用 API 支持的稳定 ID 条件，并正确转义表达式。

## 解耦、复用与扩展性评估

| 部分 | 当前评价 | 对未来扩展的影响 |
| --- | --- | --- |
| `core`、`pipeline` | Qt 边界清楚；坐标、时间、职业和轨迹公共模型集中 | 可以用于命令行和无界面分析；需要明确定义排序、缺失资源、光环和数据完整性约定 |
| `sources/local_log` | mmap 索引与解析分离，适合大日志 | 增量缓存身份不足；高级/非高级事件结构应显式区分 |
| `sources/wcl_api` | 转换器独立且已有实例、坐标、难度映射测试 | 两套下载实现分散缓存与分页责任；语义转换还不能保证和本地来源完全一致 |
| `bosses` | 注册、自动发现、通用回退及统一图元接口合理 | 添加主要使用现有图元的首领通常可以不改 UI；特殊可调模拟仍会触及公共控制器 |
| `ui` | 大部分组件消费 Analysis 和 ReplayController | 当前 session 的写入分散在两个 Board；来源选择和异步生命周期没有统一拥有者 |
| `workers` / `TaskRunner` | 分析在无 Qt 子进程中，正常路径已验证 | 初始化异常、任务失效与进程退出的恢复能力不足；扩展结果必须可序列化 |
| `tools` / 测试 | 有命令行分析、截图、打包和较多回归测试 | 截图入口缺少端到端保护；未验证本次打包产物或真实 WCL 网络路径 |

### 应保留的设计

1. **来源先归一化，首领再分析。** `FightData` 是合适的共同入口；首领机制不应自行读取 QSettings、本地文件或进行 HTTP 请求。
2. **Qt 只在 UI 层。** 当前实现符合最重要的分层要求，正常子进程分析测试提供了实际支撑。
3. **按 encounter 注册首领。** 注册冲突会报错，自动发现有编译包回退，新首领无需在主窗口增加分支。
4. **机制结果通过图元、阶段、HUD、时间轴和日志输出。** 可以继续复用，不需要为每个首领复制地图和团队框架。

### 尚未充分解耦的具体位置

- **WCL 借用本地日志模块的缓存目录**：[client.py:18](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/client.py:18)。缓存存储策略是两个来源共有的基础设施，不应由本地索引模块拥有。建议抽到小型公共存储模块，并集中版本、原子写入和损坏恢复策略。
- **WCL 界面借用本地索引中的难度文案**：[wcl_panel.py:22](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/wcl_panel.py:22)。难度 ID 与展示名称属于公共元数据，可移到 core 的公共定义，避免 UI 因本地解析器调整而受影响。
- **公共控制器含特定机制的参数和调用约定**：[controller.py:45](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/controller.py:45)、[controller.py:215](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/controller.py:215)、[playback.py:193](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/playback.py:193)。速度、面向角、加速模式等七个幽灵参数通过 `hasattr(apply_ghost_motion)` 调用，没有通用能力协议。新增另一类可调机制会要求继续修改公共 UI。建议先定义小型 capability/参数描述接口，让 Analysis 声明可调项及配置应用方式，避免为尚不存在的需求搭建大型框架。
- **基础首领接口携带 WCL 特定的数据请求协议**：[base.py:498](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/base.py:498)。由首领声明所需切片能减少下载量，当前依赖方向可接受；若未来加入第三来源或替换 WCL API，应考虑把“机制需要什么数据”和“WCL 如何查询”分开，由可选适配器翻译。
- **观测轨迹与模拟轨迹共用可变容器**：[p2.py:437](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p2.py:437)。模型用合成轨迹覆盖公共 `Tracks.tracks`，外部消费者因此取决于哪个分析模型、哪次配置修改运行过。建议区分观测数据和推断轨迹，通过组合查询接口供 UI 使用，保留数据来源与置信度。当前没有复现由此产生的独立显示错误，属于复用与维护风险。

模块之间存在有方向的依赖是正常的；目标应是清楚的职责和稳定的协议，而不是让每个文件都完全不依赖其他模块。

## 性能与维护建议

- 幽灵参数更新会从 GUI 控制器同步进入 `P2Model.set_motion()`，重建所有幽灵从出生到消失的路径：[p2.py:301](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p2.py:301)。对长战斗应测量耗时，再决定是否合并输入、后台计算、缓存参数组合。此次未做真实大日志性能基准，不能据此断言实际卡顿时长。
- 明确 FightData 的输入约定：事件排序、同时间稳定顺序、合法 actor 引用、毫秒时间范围、缺失位置/血量和来源版本。只用 dataclass 和 `dict` 不会自动保证这些条件；目前 `Targets`、二分查找和阶段模型依赖这些条件。
- 工作结果、进度、任务状态和来源身份大量使用宽泛 `tuple` / `object` / 字符串，未来字段扩展容易出现位置参数错配。为跨模块边界增加少量命名数据类和 Protocol 即可，不必重写现有全部模型。
- 按仓库约定，`Session`、`ReportInfo` 和部分阶段数据类尚未使用 `slots=True`。这是规范一致性问题，优先级低于行为修复。
- 通用 Analysis 的 `Deaths`、`Player deaths.`、`Death` 等仍是英文：[base.py:403](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/base.py:403)。未登记首领的通用回放应补齐简体中文文案。
- 保留注册与图元架构，先补足生命周期和数据语义边界，收益高于全面重构。

## 建议补充的验证

现有测试通过不等于上述流程已经被覆盖。优先添加能体现用户结果的边界测试：

| 测试主题 | 必须验证的结果 |
| --- | --- |
| WCL URL 与认证 host | 非官方 host 在任何凭证请求之前被拒绝 |
| 双来源异步完成 | 两种切换方向和相反完成顺序都不覆盖当前显示 |
| 释放与重算 | 旧成功、旧失败、旧进度均不能影响新运行 |
| 池初始化失败 | 所有等待任务有明确失败回调，能清理并按策略重试 |
| WCL 缓存 | 第二次复用、不同 host、结束时间变化和切片版本变化行为正确 |
| 两来源事件语义 | 周期治疗、层数、同时间资源变化在统一模型中保持等价 |
| 日志更新 | 真正追加继续增量；覆盖、轮换、等长变化不能沿用旧结果 |
| 机制边界 | 同时放球归属、无恐惧的施法成功、护盾仅层数事件 |
| 工具与发布 | 截图命令生成 PNG；发布包启动、子进程和首领发现冒烟测试 |

需要真实日志确认的部分，应使用脱敏、最小化、经授权的夹具或在仓库外验证，保持凭证、战斗日志和 API 缓存不进入仓库。

## 推荐修复顺序

1. F01：统一 WCL host 验证，在发出任何认证请求前拒绝不合法链接。
2. F02–F06：统一当前显示的归属及每次运行的身份，修复异常传播、连接测试和清缓存后重算。
3. F07–F11：合并 WCL 下载与缓存责任，修复来源身份和事件/采样归一化。
4. F12–F15：修正机制判定，恢复截图入口，再补发布冒烟验证。
5. 在新增首领或新来源时，按实际需求处理三个条件性风险，以及通用参数协议、共享存储和观测/推断轨迹分离。

这一顺序可以保留当前可用的架构，逐步提高正确性和扩展能力。
