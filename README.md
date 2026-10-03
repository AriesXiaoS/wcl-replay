# wcl-replay

魔兽世界战斗日志 / Warcraft Logs 回放，以及按首领拆开的机制分析（PySide6）。

## 用 uv 启动

需要已安装 [uv](https://docs.astral.sh/uv/)。Python 版本由仓库里的 `.python-version` 固定为 3.13，`uv sync` 会自行准备环境。

在仓库根目录：

```powershell
uv sync
uv run wcl-replay
```

`uv sync` 安装依赖并生成 `.venv`。之后每次启动只要第二条命令，不必先激活虚拟环境。

打开本地战斗日志：

```powershell
uv run wcl-replay D:\Logs\WoWCombatLog.txt
```

窗口最左侧可以选择日志并查看其中的战斗轮次。点某一轮才会开始计算。

## 许可

作者：伐竹取道 (AriesXiao)

本软件以 [PolyForm Noncommercial License 1.0.0](LICENSE) 授权。个人、公会等非商业用途可以查看、使用、修改和分发源码。禁止出售、嵌入收费产品，或用于收费服务。完整条款见仓库根目录的 `LICENSE`。
