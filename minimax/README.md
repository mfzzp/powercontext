# MiniMax SDD 工程上下文模块

本作品在一个可运行的 Python 工单筛选服务中增加状态筛选：以 seekdb 中实际查询到的规格、代码和测试构造上下文，交给 MiniMax 修改 `ticket_app.py`，再用同一组测试验证修改。PowerContext 保存任务进展、资料版本和测试结果，后续独立进程读取后决定继续实现、重新验证或完成。

作品位于比赛分支 `2026-competition-ecosystem` 的 `minimax/` 目录。无需 seekdb 内核开发工作区、私人运行文件或已有任务状态。已交付真实 MiniMax 调用的响应和补丁，评审可不重新调用模型而复现代码结果；实时调用入口也保留。

## 已执行的验证

真实运行环境为 Windows、Python 3.12.14、seekdb 1.4.0.0、PowerContext 1.5.0。MiniMax 使用 HTTP Chat Completions 接口、模型 `MiniMax-M3`；请求与公开响应见 `evidence/live/minimax-request.json`、`minimax-response.json`。最终交付记录的调用返回 4551 tokens，其中输入 2543、输出 2008。记录不含 Key、请求认证头或模型私有推理文本。

| 验收项 | 可核对证据 |
| --- | --- |
| 能运行 | 下列初始化及 demo 命令；`evidence/replay/summary.json` 从空任务状态复现 |
| MiniMax 实际修改 | `evidence/live/minimax-request.json`、`minimax-response.json`、`change.diff`，响应中的代码与 `after.py` 完全一致 |
| 上下文及规格更新 | `context-v1.json` 与 `context-v2.json`，同一路径的规格由 revision 1 更新至 2 |
| 数据库实际参与 | 上述 context 中的 `query_record` 含 SQL、绑定参数、真实查询返回路径、版本及摘要；`ingest-*.json` 记录导入 |
| 状态复用 | `state-empty.json`、`state-write-1.json`、`context-resumed.json`、`state-write-2.json`、`context-finished.json`；父子进程 PID 不同 |
| 代码修改有效 | 修改前目标 7 项均因断言失败，回归 6 项通过；修改后目标 7 项、回归 6 项全部通过，测试 SHA256 不变 |

`evidence/module-check.json` 另记录模块及真实数据库集成测试。Windows 原生数据库已实测；下列 Docker 配置采用官方镜像部署方式，本机没有执行 Docker/Linux 验证。正式成绩仍以评审核验为准。

## 干净环境运行

需要 Python 3.12、可连接的 seekdb/OceanBase MySQL 模式实例。以下每个命令都是单行，在本仓库 `minimax` 目录执行。建议预留 4 GB 内存和至少 6 GB 磁盘供数据库使用。

Linux 或 macOS 建立 Python 环境：

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt -c constraints.txt
cp .env.example .env.local
```

Windows PowerShell 建立 Python 环境：

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt -c constraints.txt
Copy-Item .env.example .env.local
```

下文 `python` 指上述虚拟环境解释器，可替换为 `.venv/bin/python` 或 `.\.venv\Scripts\python.exe`，无需激活。不要覆盖已有 `.env.local`；设置数据库连接参数以及实时调用时使用的 `MINIMAX_API_KEY`。文件已被 Git 忽略。

若尚无数据库，可用 Docker Compose 启动专用本地实例；已存在实例时只需配置 `.env.local`，跳过 Docker 命令：

```bash
docker compose up -d
python -m sdd_context doctor --wait 180
```

默认连接 `127.0.0.1:2970`，root 空密码，使用独立数据库 `minimax_sdd`。脚本只创建该库的 `sdd_documents` 表，并在自己的项目编号内更新资料；不会删除其他数据库。若指定普通账号，需事先创建库并授予该库的建表、查询和写入权限。Docker 配置只在本机暴露端口，使用官方 `oceanbase/seekdb:latest`；评审可改为其环境固定的镜像版本。

无需 MiniMax Key 的完整结果复现：

```bash
python -m sdd_context demo --replay evidence/live/minimax-response.json
python tools/check.py --integration
python tools/verify_evidence.py
```

该复现仍真实导入、查询 seekdb，真实保存及跨进程读取 PowerContext 状态，并执行修改前后同一组测试。仅模型请求由已交付的真实响应代替，输出明确记录 `model_invoked: false`，不冒充再次调用模型。

重新进行真实模型修改：

```bash
python -m sdd_context demo
```

`MINIMAX_BASE_URL` 示例为本次已成功调用的国内地址 `https://api.minimaxi.com/v1`，`MINIMAX_MODEL=MiniMax-M3`。如账号指定其他国内/国际站接口或模型，应按控制台修改这两项；不可将 Anthropic 协议地址填入本模块的 Chat Completions 配置。一次示例只发起一个有上限的代码生成请求，不自动重试计费请求。

## 输入输出及独立调用

`sample/task.json` 是任务描述、查询词和验收所需来源清单；`sample/manifest.json` 指定真实文件路径和 `spec/code/test` 类型。所需来源列表只用于检查检索是否遗漏，不参与替换数据库查询结果。`sample/specs/billing.md` 为无关资料，当前任务检索不会选入。

| 命令 | 输入 | 输出 |
| --- | --- | --- |
| `doctor` | `.env.local` 数据库配置 | 初始化表并输出数据库版本，失败返回非零 |
| `ingest` | 项目编号、工程根目录、完整资料清单 | 内容、SHA256、种类和递增版本写入 seekdb；输出导入记录 |
| `context` | 项目编号、任务 JSON、PowerContext 状态文件 | 规格/代码/测试正文、路径、行号、版本、SQL记录、已完成/待办/测试状态、下一步动作 |
| `demo` | 可选运行名称或真实响应回放路径 | 新建隔离工程，执行完整流程，打印 evidence 路径和验证摘要 |

例如指定一次运行名称：

```bash
python -m sdd_context demo --run review-example --replay evidence/live/minimax-response.json
python -m sdd_context context --project review-example --task sample/task.json --state .runtime/review-example/progress.sqlite --output .runtime/review-example/resumed-again.json
```

可编辑 `.runtime/review-example/project/specs/status.md`，然后重新导入及查询，结果必须反映实际新内容，旧完成状态会改为 `revalidate_changed_sources`：

```bash
python -m sdd_context ingest --project review-example --root .runtime/review-example/project --manifest sample/manifest.json --output .runtime/review-example/reimport.json
python -m sdd_context context --project review-example --task sample/task.json --state .runtime/review-example/progress.sqlite --output .runtime/review-example/updated-context.json
```

`ingest` 将清单视为该项目的完整快照；移出清单的资料会在该项目内删除。未变化内容保留版本号。文件必须位于工程根目录内，每份上限 100 KB。检索使用参数化 SQL，最多 16 个词、返回 12 个来源，按匹配词数排序；该实现面向小型验证工程，不声称具备向量语义检索能力。

## 验证样例与重置

目标：实现 `filter_tickets(tickets, *, status=None, limit=None)` 状态筛选，比较双方的 `strip().casefold()`，先筛选再 limit。未知或空白状态返回空列表。原有行为：无筛选保持顺序、返回新列表；limit=0 返回空列表；非法 limit 报 ValueError；不修改输入。

必须进入上下文的真实来源为 `specs/status.md` 的 S1–S4、R1–R5，`ticket_app.py` 的 `filter_tickets`，`tests/test_status.py` 和 `tests/test_regression.py`。目标与回归测试使用固定的工单字典作为初始数据，无需外部业务数据。规格 v1/v2 均交付；demo 先导入 v1，再以 v2 替换同一路径，只有重新查询出的 v2 用于 MiniMax 修改。

业务初始代码在 `sample/before/ticket_app.py`；真实模型产生的最终代码在 `sample/after/ticket_app.py`，两个阶段使用相同的 `sample/tests/`。`evidence/live/before.py`、`after.py` 及 `change.diff` 可与响应逐字核对。测试日志记录命令、退出码、被测代码 SHA256 和测试文件 SHA256。

每次 demo 默认产生唯一项目编号、新工作目录、新 PowerContext 文件，因此直接重跑即可从空状态重新验证，无需清理旧证据。指定 `--run` 时必须用未占用的名称。需要清理时可删除自己选定的 `.runtime/<run>`；数据库只需按相同 `project_id` 清理 `sdd_documents`。Docker 验证结束可运行 `docker compose down`，该示例容器没有持久化卷。

## 技术分工与限制

PowerContext 使用公开发布的 Python SDK `powercontext[builtin]==1.5.0`，通过其 `SQLiteProfile`、`ArtifactRepository` 和 `sdd-progress` Artifact 保存并追加任务状态版本。SQLite 仅承担 PowerContext 状态存储；工程资料始终从 seekdb 查询。没有用自写 JSON 文件假冒 PowerContext。

模块只允许模型改指定纯函数文件，并拒绝导入、I/O、未知函数调用等不符合示例的输出，且设置子进程超时；它不是通用不可信代码执行平台。模型输出无效、接口报错或测试失败都会返回非零并保留已有证据，不修改测试来通过。

参考：[MiniMax 官方兼容接口文档](https://platform.minimax.cn/docs/api-reference/text-openai-api)、[PowerContext 1.5.0](https://pypi.org/project/powercontext/1.5.0/)、[seekdb 官方 Docker 说明](https://github.com/oceanbase/docker-images/blob/main/seekdb/README.md)。业务样例和上下文模块为本作品新增，第三方组件均通过公开依赖调用，未修改其源码。结构及技术分工见 `TECHNICAL.md`。
