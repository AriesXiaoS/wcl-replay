# 第二轮全量代码审查 · 2026-10-03

## 结论与范围

现有 `来源 → FightData → Tracks → Analysis → UI` 分层适合继续扩展。新首领可以复用解析、轨迹和绘制接口，
分析层也没有直接依赖 Qt。不过，参数改变后的结果通知、玩家派生轨迹、首领加载故障隔离仍不完整，
不能据此认定所有模块已经完全解耦、所有逻辑都已可靠。

本轮检查当前工作区中的 **52 个源码 Python 文件（10427 行）及 6 个工具脚本**，并复核项目配置、
扩展文档和相关测试。以上一轮修复后的状态为基础，未把旧审查中的已修问题重复列为未修问题。
发现 **14 项可复现问题：1 项 P1、8 项 P2、5 项 P3**。N06、N07、N14 包含扩展条件下的复现，
其触发条件在下文说明。

本轮只新增审查报告，未修改生产代码。全部复现使用仓库外的合成日志、模拟 API、独立 Qt 控件和隔离缓存，
没有读取真实 Client Secret、连接 WCL、修改正在运行的程序或执行打包清理。

- P1：应优先处理，存在界面异常或长时间无响应的风险。
- P2：影响正确性、恢复能力或公共扩展接口，应在对应功能继续扩展前处理。
- P3：局部显示、长期资源占用或调试工具准确性问题。

## 可复现问题

### N01 · P1 · 输入坐标没有有限数值检查，地图绘制没有工作量上限

位置：[parser.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/parser.py:138)、
[convert.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/convert.py:182)、
[map_view.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/map_view.py:305)。

本地解析允许 `float("nan")` 进入 Sample，WCL 转换也未验证位置与朝向是否有限。
一个包含 NaN 玩家坐标的合成日志正常通过索引和解析，随后 `Tracks.bounds` 得到 NaN，
地图网格所用的 `math.ceil` 抛出 `ValueError: cannot convert float NaN to integer`。
这类错误发生在 GUI 绘制过程中，后台任务失败处理无法隔离它。

有限但异常大的位置同样存在风险：两个相距 100000000 码的样本使相机范围预计生成 **19600004 条网格线**。
实际调用 `_draw_arena` 时，审查用绘制代理在第 201 次 drawLine 主动中止，确认代码持续逐条绘制，
没有随屏幕大小调整密度或限制次数。没有实际运行数千万次绘制去冻结界面。

建议：在来源适配和公共模型边界验证坐标、朝向及时间；非有限样本拒绝或跳过并记录诊断。
地图根据视野和像素选择网格间距，对网格线与路径点数设置预算，避免把日志数值直接变成无上限的绘制循环。

### N02 · P2 · 部分损坏缓存仍会阻止恢复

位置：[events.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/events.py:56)、
[index.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/index.py:336)。

把有效事件 gzip 缓存截掉最后 8 字节，再加载时抛出 EOFError。当前异常列表不包含 EOFError，
**重新下载调用次数为 0**，继续点击仍读取同一坏文件。现有损坏缓存测试没有覆盖这种截断形式。

另外，本地索引缓存内容为合法 JSON `[]` 时，调用 `cached.get` 抛出 AttributeError，不能回退到重新扫描。
这说明目前只验证了部分解析错误，没有完整验证缓存的根对象和字段结构。

建议：先验证缓存对象及字段类型；把截断 gzip 的 EOFError 纳入可恢复的缓存读取错误。
损坏缓存应当作未命中，重新生成，保留业务代码本身的异常可见性。

### N03 · P2 · 持续写入日志的半行会让整次索引失败

位置：[index.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/index.py:191)。

日志末尾刚写到 `ENCOUNTER_END,...,1,` 时，字段数已经达到 7，但持续时间还是空字符串。
`int(f[6])` 立即抛出 ValueError。游戏仍在写文件时点击刷新可触发这一窗口，整个列表读取因此失败。
START 中的数字字段以及 COMBATANT_INFO 的字段访问也缺少完整的格式检查。

建议：未完成的尾行等待补齐后再解析；完整但格式错误的关键记录给出行位置和原因。
可跳过的普通坏事件记录计数诊断，不让一条坏事件无说明地破坏整场索引。

### N04 · P2 · 凝视的来源独立语义没有传递到团队框架

位置：[base.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/base.py:509)。

P2 幽灵模型使用 `aura_intervals(..., source_independent=True)`，团队框架构建 FrameAura 时仍采用默认目标归并。
复现：两个幽灵分别在 6100ms、6200ms 凝视同一玩家，第一个在 7100ms 解除，第二个持续到 8100ms。
**7500ms 幽灵模型仍有一个目标为该玩家的凝视，团队框架图标却为空**。

建议：让 FrameAura 声明来源策略，独立计算实例后将“至少一个实例仍存在”合并为显示状态。
机制模型、团队框架和其他光环消费者使用同一语义。

### N05 · P2 · 声明的默认参数与实际分析模型不一致

位置：[module.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/module.py:74)、
[p2.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/coiled_altar/p2.py:158)、
[controller.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/controller.py:280)。

Analysis 声明 `ghost_motion="accel"`，P2Model 初始化却生成 constant 模型。
`pipeline.analyze` 返回后，参数字典是 accel，实际模型是 constant；只有界面 set_session 推送参数后才重建为 accel。
合成数据中，同一幽灵在 6600ms 的位置从 `(1158.6, 1.5)` 变为 `(1158.6, 1.2)`，期间没有修改任何参数。

这使直接使用分析接口、后台计算结果和最终界面结果存在差异，也使默认参数字典不能准确描述已生成的结果。

建议：首领分析完成前就应用声明的默认参数；需要配置的计算通过显式参数传入。
将“返回结果与 parameter_values 一致”设为分析接口的约定，并覆盖无界面路径。

### N06 · P2 · 玩家派生轨迹会复用观测轨迹的下标而越界

位置：[tracks.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/core/tracks.py:141)、
[tracks.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/core/tracks.py:153)。

`_player_lives` 中的 lo/hi 是按观测 Track 生成的。set_derived 给玩家替换为样本数量不同的轨迹后，
pose 优先取派生 Track，却把旧 lo/hi 交给 `_pose_player`。
两条观测样本、一个死亡事件、仅一条派生样本的复现抛出 `IndexError: index 1 is out of bounds`。

当前盘卷祭坛只给 NPC 写派生轨迹；未来首领推断玩家位移或载具路线时会触发这个公共接口问题。

建议：明确死亡状态与位置各自的数据来源；插值下标始终属于所查询的 Track。
如暂时只支持 NPC，应在 set_derived 明确限制并写入扩展文档，避免给调用方一个不能安全使用的玩家接口。

### N07 · P2 · 首领注册缺少原子性，发现过程缺少故障隔离

位置：[registry.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/registry.py:17)、
[registry.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/bosses/registry.py:26)。

register 按 encounter ID 逐个写注册表。已有 ID 90001，新增模块声明 `(90002, 90001)` 时，
后一个 ID 冲突抛错，但 **90002 已留在注册表中**。注册失败不能恢复到调用前状态。

discover 会一次导入全部首领包，一个包抛出 ImportError 就终止整个发现过程。
故障注入后，对完全无关的、未登记 encounter 调用 module_for，也得不到通用 BossModule，而是抛出同一 ImportError。
新增一个有错误的首领包会扩大故障范围。

建议：注册前先检查全部 ID，通过后整体提交。按模块隔离发现失败并记录诊断，
为受影响遭遇战明确显示模块错误；无关首领和通用回放继续可用。

### N08 · P2 · 缓存写失败会把成功下载或扫描变成业务失败

位置：[events.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/events.py:68)、
[index.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/index.py:379)、
[client.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/wcl_api/client.py:125)。

下载已经成功后，模拟 write_json 抛出 PermissionError，cached_events 不返回已下载事件而直接失败。
本地索引、OAuth token 写盘也把成功的主流程与缓存持久化绑在一起。
缓存目录权限、磁盘满或文件占用会让用户看到分析失败，下一次可能再次消耗下载额度。

建议：明确可选缓存的失败策略。可保留已取得的结果继续工作，提示缓存未保存；
真正需要持久化成功才能完成的操作则给出专门的存储错误，不混入机制分析错误。

### N09 · P3 · 层级拖动动画结束后，Qt 对象仍被父对象持有

位置：[stack_panel.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/stack_panel.py:30)。

每次滑动创建以列控件为父的 QPropertyAnimation。finished 回调只从 `_anims` 字典删除，
没有 deleteLater 或 DeleteWhenStopped，停止旧动画时也一样。
连续完成 12 次动画后，活动字典为 0，但列控件仍持有 **12 个 QPropertyAnimation**。
长时间反复拖动时对象随操作次数积累。

建议：完成和被替换的动画都释放 Qt 对象，同时整理 drop 动画的生命周期。

### N10 · P3 · 远处标记可以逐个扩大相机，结果还受标记顺序影响

位置：[map_view.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/map_view.py:48)。

frame_bounds 判断标记是否靠近玩家时，使用的是已经被先前标记扩大的范围。
玩家范围 `(0,1,0,1)`、标记 `(70,0)、(140,0)、(210,0)`，正序得到 x 范围 `[-6,216]`，
逆序得到 `[-6,76]`。同一组标记产生不同相机，远处标记可沿链被纳入。

建议：使用不可变的原始玩家范围筛选附近标记，再统一计算包围盒。

### N11 · P3 · 未结束战斗在列表中的时长始终为零

位置：[index.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/sources/local_log/index.py:185)、
[log_panel.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/log_panel.py:31)。

索引为未关闭 entry 保留 duration_ms=0，虽然内部已能推算 `_fight_duration`，却没有更新用于列表的字段。
合成的 10 秒未结束战斗，解析结果为 10000ms，列表仍显示 `0:00 · 进行中`。

建议：在索引快照里保存已观察到的时长，同时用 closed 区分“目前时长”与最终时长。

### N12 · P3 · API 预算探测器把本小时历史消耗算入本次运行

位置：[probe_wcl_budget.py](D:/Code/魔兽世界wcllog分析/tools/probe_wcl_budget.py:124)、
[probe_wcl_budget.py](D:/Code/魔兽世界wcllog分析/tools/probe_wcl_budget.py:339)。

main 用 `Budget(0,0,max_points)` 创建预算，第一次元数据查询得到本小时已花点数后，
spent 更新但 start 仍为 0。模拟服务返回本小时 2400 点，本次上限为 1200，
工具只做一次元数据查询就报告“本次已用 2400 点”并拒绝下一次查询。
小时额度重置时，相减计数也会失真。

建议：从首次额度响应建立基准，按本次请求增量累计，并处理额度周期切换。
测试应同时覆盖已有历史用量和计数重置。

### N13 · P3 · API 探测工具仍使用旧分页和去重策略

位置：[probe_wcl_budget.py](D:/Code/魔兽世界wcllog分析/tools/probe_wcl_budget.py:466)。

生产下载器已经修复空页有游标、游标不推进及超过页数上限的处理，探测器仍复制旧算法：
遇到空 batch 就退出，不推进和 40 页截断可以返回部分结果，dedupe 还把同一切片内完全相同的重复事件全部压成一条。
复现的第一页为空但 nextPageTimestamp=100，第二页有事件，工具只查询一次并返回 0 条。

这影响探测结果的事件数和覆盖率，不会直接改写生产事件缓存。

建议：复用生产分页规则或公共分页组件；额度计量通过适配器接入，避免两套逻辑继续漂移。

### N14 · P2 · 参数改变后的结果没有完整通知界面消费者

位置：[controller.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/controller.py:247)、
[panels.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/panels.py:107)、
[timeline.py](D:/Code/魔兽世界wcllog分析/src/wcl_replay/ui/timeline.py:37)。

公共 apply_parameters 允许首领重建结果，但控制器只发 parametersChanged 和 timeChanged。
事件日志时间索引、时间轴高度以及图层默认值只在 sessionChanged 时重建。

用新增首领的合成 Analysis 复现：参数变化把日志从 1000ms 的“旧事件”改为 2000ms 的“新事件”，
时间轴从 1 条变为 4 条。分析数据已改变，**日志面板仍保留旧文字和 `[1000]` 的索引，
时间轴高度仍为 50，新增 default_on=False 图层却被 layer_on 当成打开**。
当前盘卷祭坛参数主要改变轨迹；以后参数影响日志、阶段或时间轴时会出现该问题。

建议：增加明确的分析结果变化通知，界面重新同步依赖结果的索引、尺寸和图层，
同时保留当前播放位置、选择和用户已有的图层开关。
扩展测试除验证图元外，还应验证参数确实可以改变日志、时间轴和默认图层。

## 解耦、复用与未来扩展

| 边界 | 当前评价 | 后续重点 |
| --- | --- | --- |
| 本地/WCL → FightData | 分层清楚，核心计算可复用，两种来源共享同一模型 | 补齐输入验证和结构不变量；来源缺字段时应有明确诊断 |
| FightData/Tracks → 首领模型 | 不直接依赖 Qt，观测与派生轨迹已分开 | 修复玩家派生接口 N06；约定时间排序、死亡与插值的语义 |
| 首领 Analysis → UI | 图元、HUD、状态、日志、FrameAura、参数描述可复用 | 修复 N04、N05、N14，完善结果一致性与失效通知 |
| 首领注册与自动发现 | 插入普通新首领便利 | N07 表明失败仍会跨模块传播；新增模块不应成为全部回放的单点故障 |
| WCL 来源与首领切片 | 目前是有意的适配关系，下载器复用良好 | WclSlice 和 wcl_slices 把公共首领接口绑到 WCL；第三来源或更多传输协议应放在适配层，不扩大到阶段模型 |
| 通用界面与首领专有概念 | 泛化参数栏已改善解耦 | 控制器仍有 ghost 兼容字段/信号；stack_order 只特别识别 group="ghost"，自定义 group="illusion" 会变回 npc 分类 |
| 生产逻辑与调试工具 | 部分重复仍存在 | N12、N13 说明工具的独立算法会漂移；共享来源解析和分页，调试脚本只做展示/计量 |

普通新首领若只输出现有图元和静态时间轴，可以沿现有方式增加子包。
涉及参数动态改变日志/图层、自定义单位分组、玩家派生路径或模块失败降级时，应先补强上述公共接口。
建议按这些具体需求扩展契约，暂不需要整体重写或引入复杂插件框架。

## 另外需要管理的风险与验证缺口

### R01 · 后台任务没有取消与总量控制

本地缓存场数限制主要约束已完成结果；全是 running 时 `_make_room` 无法减少任务。
WCL 结果按现有设置不自动清理，每次同一 URL 查询也有独立记录。
行的 release/remove 只使回调失效，没有取消 TaskRunner 中的计算和 HTTP 下载。
反复删除、重算仍占用后台进程、内存，并可能继续消耗 API 点数。
PullLoads 连续提交 50 个不同键可保留 50 个 running，当前没有队列上限。

这是当前行为策略和容量风险，不能把“旧回调被忽略”当成“任务已取消”。建议为任务返回可追踪句柄，
增加排队上限、取消/超时，以及同输入下载的并发合并；配额查询和连接测试可使用独立优先级。
内存策略应涵盖 WCL、收藏结果和运行中任务，并向用户展示实际占用。

### R02 · 索引缓存命中仍需通读旧文件

index_log 的命中判断通过 `_prefix_digest` 校验完整旧前缀，命中时也遍历全部旧字节；
增长扫描还会重新搜索换区上下文和生成分析指纹。
这是为了检测同路径文件被改写而付出的成本，不能描述为只读取新增字节。
大日志的真实性能仍需测量，尤其是网络盘和机械盘；完整前缀哈希期间也没有扫描进度反馈。
优化应保留替换检测要求，不能只为速度恢复上一轮的错误缓存复用。

### R03 · 部分机制输出仍把启发式结果当成确定结论

`_fixate_ends` 将不符合已有时间窗口的解除默认标成 reached；`_pick_popped` 在扇形内候选不足时会补入其他球；
炸弹模型以光环结束推定爆炸。它们可能符合已观察日志，但缺失事件、死亡、其他难度和热修会改变适用性。
本轮没有据此断言真实游戏必然算错。

建议为推断保留依据、未知结局和置信状态，尽量结合实际伤害、成功施法及来源信息验证。
用脱敏真实日志对照机制，再决定哪些假设可以成为硬规则。
合成测试证明算法按设定运行，不能证明设定覆盖全部游戏机制。

### R04 · Qt 独立性还需验证进程入口

AST 检查显示 core、bosses、pipeline、workers 共 20 个文件没有直接 Qt 导入，pytest 的 worker 探针也通过。
但 app.py 顶层导入 GUI，tools/wcl_replay_entry.py 又顶层导入 app.main。
用与该脚本一致的顶层入口构造实际 spawn 探针，子进程返回 `imported_qt=true`。

这证明直接模块边界与启动过程的边界不同。未据此认定 Nuitka 发布包必然存在同样行为，发布包还没有做入口验证。
建议将 GUI 初始化相关导入延后到 main 中、放在 freeze_support 后，并为实际源码/发布入口分别做 worker 冒烟检查。

### R05 · 安全与交付边界

- URL/host 校验限制 HTTPS 官方域名边界，当前静态检查和已有模拟测试未发现任意链接可把 OAuth 凭证发往外部域名的路径。
- 未发现日志/缓存输入进入 eval、exec、外部 pickle 反序列化或拼接 shell 执行的路径。进程队列 pickle 用于应用自己创建的子进程通信。
- 按项目约定，Secret 保存于 QSettings，token 保存于本机 JSON；它们没有额外加密。这依赖本机账户权限，后续有更强凭证保护需求时可迁移到系统凭证存储。
- 缓存缺少磁盘容量/保留期限控制；多场高事件量分析同时持有事件、样本、numpy 轨迹及进程序列化副本，峰值内存需要大日志基准。
- 未做真实 WCL 请求、发布包运行、全量依赖漏洞扫描或真实大日志压力测试；这些不能由 197 个单元/回归测试替代。

## 验证记录与复现文件

- 完整 pytest：**197 passed in 4.86s**。
- `ruff check .`：通过。
- `ruff format --check .`：93 个文件符合格式。
- `git diff --check`：通过；Git 有现有 LF/CRLF 提示。
- Qt 边界 AST：20 个核心/首领/管线/worker 文件未发现直接 Qt 导入。
- 独立复现脚本验证以上缓存、半行、凝视、默认参数、派生轨迹、注册、动画、相机、时长、工具和结果通知问题。

复现材料均在本轮隔离目录：

- [复现脚本](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2_repro.py)
- [复现结果 JSON](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2-artifacts/results.json)
- [进程入口探针](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2_entry_smoke.py)
- [入口探针结果](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2-artifacts/entry-smoke.json)
- [本轮 58 个源码/工具文件 SHA-256](C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2-artifacts/source-hashes.json)

命令使用现有 uv 环境及隔离临时目录：

```powershell
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync pytest -q -p no:cacheprovider --basetemp C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2-baseline
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff check .
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff format --check .
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync python C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/review2_repro.py
```

修复顺序建议：N01 → N02/N03/N08（输入与恢复）→ N04/N05（现有机制结果）→
N07/N06/N14（扩展契约）→ R01（后台资源控制）→ 其余显示与工具问题。
