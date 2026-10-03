# 代码修复记录 · 2026-10-03

本次按 [审查记录](code-review-2026-10-03.md) 修复 F01–F15，并为 R01–R03 明确行为策略。
保留已有未提交修改，沿用 `FightData → Tracks → Analysis → UI` 的分层，没有更换依赖。
测试使用合成日志、模拟 API 和隔离设置，日志、凭证和下载缓存不进入仓库。

## 行为修复

| 问题 | 修复后的行为 | 主要回归测试 |
| --- | --- | --- |
| F01 | URL、API 域名及客户端初始化共用 HTTPS 官方域名边界校验；拒绝 userinfo、恶意后缀及非标准端口，在创建传输前完成检查 | `test_source_regressions.py` |
| F02 | 控制器保存当前来源；隐藏面板可保存后台结果，仅当前来源的选中结果可更新回放 | `test_loading_regressions.py` |
| F03 | 每次计算获得单调递增的运行 token；旧完成、旧错误及旧进度不能影响释放后的新运行 | `test_loading_regressions.py` |
| F04 | 完整池初始化及清理均有异常处理；线程构造/启动失败可重试，发布池后再启监听；任务参数、返回值预先验证可序列化；监测子进程退出并通知所有等待任务，新提交可重建池 | `test_loading_regressions.py`、`test_loader_startup.py` |
| F05 | 连接测试提交后台进程；按钮显示进行状态，结果回到 GUI 线程；关闭后的旧回调失效 | `test_loading_regressions.py` |
| F06 | WCL 清缓存后的列表行和失败行均可点击重算，复用原链接并生成新运行身份 | `test_loading_regressions.py` |
| F07 | 两个 WCL 入口复用同一分页及 gzip 缓存模块；缓存区分 host、报告、fight、时间范围、版本和切片策略；损坏缓存重新下载 | `test_source_regressions.py` |
| F08 | 本地及 WCL 仅删除完整字段相同的样本，同毫秒血量、朝向变化全部保留 | `test_source_regressions.py` |
| F09 | 校验文件身份及已扫描前缀指纹后才增量扫描；战斗内容和分析上下文各有指纹，替换或上下文变化不复用旧分析 | `test_source_regressions.py`、`test_index_context.py` |
| F10 | 每条事件识别是否带高级资源块，普通日志也正确解析伤害/治疗数值；没有位置时保留事件 | `test_source_regressions.py` |
| F11 | WCL 周期治疗与伤害转换为统一的周期事件类型，HoT 不再改变目标推断 | `test_source_regressions.py` |
| F12 | 球体掉落结合颜色、时间和携带者位置匹配，并限制距离；无法区分的同等候选保留未知归属 | `test_mechanic_regressions.py` |
| F13 | 同施法者、同技能的成功事件结束哀嚎施法；成功结局与附近的恐惧人数分别记录 | `test_mechanic_regressions.py` |
| F14 | 护盾初始应用、增层、减层和移除都有明确处理，只有层数事件也能建立状态 | `test_mechanic_regressions.py` |
| F15 | 截图工具使用窗口明确持有的状态组件；校验轮次及 PNG 保存结果，支持独立设置文件 | `test_loading_regressions.py` |
| R01 | `aura_intervals` 提供可选来源独立策略，默认保持按目标归并；幽灵凝视启用来源独立；无来源的整体移除关闭该目标该法术的全部实例 | `test_mechanic_regressions.py` |
| R02 | 不再按“短时间清除多个标记”推定换区；仅依据附近明确的 `ZONE_CHANGE` 上下文标注卸载；增量追加也重算该上下文 | `test_mechanic_regressions.py`、`test_index_context.py` |
| R03 | 球体查询从报告 NPC gameID 获取当地语言名称并转义，同时包含技能 ID 条件作为兜底 | `test_source_regressions.py` |

分页还有两项补强：空页存在后续游标时继续查询；游标不推进或超过限制时明确失败，
不将截断数据写为完整缓存。切片重叠去重保留单个切片内部实际存在的重复事件数量。
API 下载、配额查询和连接测试在成功或失败后均释放客户端连接。

## 解耦与复用

- 新增不依赖 Qt 的公共 `storage.py`，统一用户缓存目录及原子 JSON/gzip 写入；WCL 不再借用本地日志模块的存储工具。
- 难度文案归入 `core/difficulty.py`，来源和界面复用公共定义。
- `Tracks` 分开保存观测轨迹和派生轨迹；回放查询组合结果，重复机制分析仍从观测数据开始。
  无来源的凝视移除也能确定幽灵寿命；派生轨迹可声明有效区间，通用地图显示与模型寿命一致，明确死亡优先截断。
- `Analysis` 提供参数描述与配置接口，首领声明可调项，公共界面依据描述生成控件；保留已有幽灵调参入口和设置键的兼容性。
- `Session`、`ReportInfo` 和阶段数据类补齐 `slots=True`；通用死亡时间轴及日志补齐简体中文。
- 自动注册、通用首领回退、图元输出及无 Qt 的分析进程保持原有边界。

## 验证与边界

最终工作区验证结果：

- 完整 pytest：**197 passed**，原有 104 个测试继续通过，新增 **93 个回归用例**。
- `ruff check .`：通过。
- `ruff format --check .`：通过，93 个文件符合格式要求，包含文档内 Python 示例。
- `git diff --check`：通过。
- AST 检查：`core/`、`bosses/`、`pipeline.py` 和 `workers.py` 共 20 个文件未发现直接 Qt 导入；实际子进程与序列化测试继续通过。
- 截图 CLI 使用合成日志和独立设置输出两张有效 **1500 × 980 PNG**；通用参数栏上线后尺寸断言及视觉检查仍通过。

新增回归文件：`test_source_regressions.py`、`test_loading_regressions.py`、
`test_mechanic_regressions.py`、`test_index_context.py`、`test_cache_identity.py`、
`test_analysis_parameters.py`、`test_loader_startup.py`。用例包含真实子进程崩溃及恢复、
不可序列化返回值、线程与队列初始化/清理失败、双来源完成顺序、旧回调拒绝、
新首领描述的参数实际影响图元，以及旧设置的兼容恢复。

合成日志、API 缓存、设置和 pytest 临时文件均位于仓库外的隔离测试目录。当前环境默认 uv 缓存和 pytest 临时目录
不可访问，因此使用已有环境，不安装或升级依赖：

```powershell
$fixTemp = 'C:/Users/AriesXiao/.codex/visualizations/2026/10/03/01a10183-8d5c-7302-b129-c20d591e366f/final-fixes-2'
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync pytest -p no:cacheprovider --basetemp $fixTemp
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff check .
uv --cache-dir .ruff_cache/uv-audit run --offline --no-sync ruff format --check .
```

保留以下实际限制，避免把合成回归测试等同于真实游戏数据的完整验证：

- WCL 同毫秒跨切片事件没有稳定全局序号时，保留所有快照仍不能恢复服务器的真实先后顺序；同毫秒最终血量可能受 API 返回及切片顺序影响。
- 来源独立光环的无来源移除采用整体清除策略；换区前后 1000ms 的标记移除视为卸载。新首领若有不同语义，应明确调整策略并使用脱敏日志验证。
- 球体匹配缺少位置或证据相等时可能保留未知；不将列表顺序当成确定归属。
- 未执行真实 WCL 认证/下载、真实大日志性能基准或完整 Nuitka 打包。幽灵路径参数改变仍同步重建，是否需后台计算应按真实战斗耗时判断。
- 首领声明 WCL 切片的适配方式保持原设计；新增第三来源时，再按实际数据需求扩展适配层。现有修改不意味着所有未来机制都无需增加公共类型。
