# 扩展首领分析

新首领放在 `src/wcl_replay/bosses/<name>/` 子包。在 `__init__.py` 导入用 `@register`
登记的 `BossModule` 子类，并声明 `encounter_ids`。自动发现负责接入，未登记的遭遇战继续使用通用回放。

两种来源都先转换为 `FightData`。机制模型只依赖公共数据，不读取 QSettings、不下载 API 数据、
不导入 Qt。将判断结果通过 `Analysis` 的图元、阶段、时间轴、HUD、状态与日志接口交给界面。
坐标用世界坐标，时间用战斗开始后的毫秒。
同一毫秒内仍可能有多条按发生顺序排列的事件。匹配读条结算时按事件位置查找后续记录，
不要用时间加一排除同毫秒事件；派生坐标在同一时间应保留最后一次观测。

## 可调参数

在 `Analysis.parameters` 声明 `AnalysisParameter`，公共参数栏会生成数字或选项控件。
参数描述及配置接口不依赖 Qt，新首领不需要增加公共界面分支。

```python
from wcl_replay.bosses.base import Analysis, AnalysisParameter


class ExampleAnalysis(Analysis):
    parameters = (AnalysisParameter("radius", "作用半径", 8.0, minimum=1.0, maximum=40.0, suffix="码"),)

    def apply_parameters(self, values):
        super().apply_parameters(values)
        radius = self.parameter_values["radius"]
        # Use radius to rebuild this mechanic's derived results, or read it in overlays_at.
```

数字参数可声明步长、小数位、范围与提示；选择参数用 `choices` 声明值和中文文案，
`visible_when` 控制依赖其他参数的显隐。通常按 encounter ID 保存设置，避免不同首领参数重名；
显式 `settings_key` 用于兼容旧设置。盘卷祭坛保留原来的七个 `ghost_*` 键。

`pipeline.analyze(data, parameters={...})` 支持无界面配置；返回时声明的参数必须与已生成模型一致。
管线先应用声明的默认参数或传入值，再调用 `Analysis.refresh_indexes()` 排序日志、阶段并同步光环和目标索引。
管线通过 `BossModule.analyze_with_parameters(data, tracks, parameters)` 构造结果；默认实现仍调用原有
`analyze(data, tracks)`，再应用参数。计算成本较高的首领可覆盖该接口，把最终参数传入结果构造器，
在参数确定后只生成一次派生数据，避免先按默认值生成再重算。返回的参数与模型必须一致。
首领类直接构造结果时也应在阶段模型初始化完成后应用默认值，避免把正确性依赖放到界面初始化中。
覆盖 `apply_parameters` 时允许重建日志、时间轴、图层和图元；界面通过 `analysisChanged` 同步这些结果，
保留播放位置、单位选择、相机缩放和已有图层开关。新增图层使用自身的 `default_on`。
团队框架光环索引和 `Analysis.targets` 会复用未变的事件。替换或追加事件时会自动重建；
修改光环声明也会重建光环索引。若直接修改现有事件对象，调用
`refresh_indexes(invalidate_auras=True)` 显式刷新光环和目标索引。
目标索引只保留目标变化，并随分析结果从工作进程传入界面；`Session` 直接复用该索引。

## 观测与推断

机制分析读取 `tracks.observed_track(actor_id)`，需要展示模拟结果时用 `set_derived` 写入派生轨迹。
`track`、`pose`、`position` 优先返回派生结果供回放使用，原始 `FightData.samples` 和观测轨迹保持可查。
重复分析或配置变化应从原始观测重新开始，避免模拟结果成为下一次推断的输入。
推断出明确出现/消失时间时，可向 `set_derived` 传入 `active_span=(start, end)`；
通用可见性查询尊重此区间，避免用采样尾部或活动宽限期延长模型认定的寿命。

玩家也可以使用派生轨迹。死亡、复活以及有观测时的血量仍依据原始观测；位置与朝向使用派生样本。
插值被限制在同一次生命中，死亡后保持该次生命的最后位置，复活后从新生命开始。
绘制玩家移动路线时可通过 `tracks.alive_spans(actor_id, start, end)` 按生命拆分区间，
并用 `tracks.position` 读取区间内的位置，避免路线连接到死亡后的另一次生命。
某次生命缺少派生样本时回退到观测位置。`set_derived` 要求单位已登记、轨迹非空；
`active_span` 主要用于 NPC 的出现与消失，玩家在团队框架中持续存在，死亡由 `is_dead` 判定。
样本的时间与资源值应为有效整数，坐标和朝向必须有限；来源和 Tracks 会跳过无效记录并计数到
`FightData.diagnostics`，通用日志展示这些提示。

光环默认按目标与法术合并。确实按来源独立存在的效果启用
`aura_intervals(..., source_independent=True)`；该策略将无来源移除解释为目标上该法术的整体清除。
新机制若不符合此语义，应在模型中明确处理，并增加合成及脱敏日志测试。

对应团队框架效果也应在 `FrameAura(..., source_independent=True)` 声明相同策略。
多个独立实例合并成“至少一个仍存在”的图标显示。
启发式匹配需要保留推断依据；缺少观测时输出未知或推定，避免把未知解除归因为追上玩家。

自定义单位层级可用 `UnitStyle(group="illusion", group_label="幻象")` 声明；
通用地图与层级面板会识别该分组，不需要添加首领专有界面分支。
注册会先检查全部 encounter ID，发现失败会回滚该包的注册并记录诊断；
未注册遭遇战使用通用回放，日志明确提示加载失败的模块。修复模块后重新启动应用以重新发现。

WCL 下载仍由首领声明切片，公共下载器负责分页、重叠去重、缓存和转换。
新增来源应先适配 `FightData`，再评估是否需要扩展公共事件或图元类型。

后台任务返回 `JobHandle`。释放记录、切换日志及关闭连接测试应调用 `cancel()`。
任务默认上限 16 个（包括运行中、排队中及等待取消确认的任务），默认 900 秒后请求取消。
取消采用协作检查：解析进度、文件扫描、幽灵模拟和 HTTP 请求边界会检查取消信号，
已经发出的 HTTP 请求需等待返回或网络超时；新增长循环应调用 Qt 无关的 `check_cancelled()`。
