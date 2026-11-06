# 记仇便利店：阿柜真的记得你

玩家用一句话对午夜便利店的店长做点什么。可以换价签、给商品起外号，也可以道歉、整理货架。再次使用同一个玩家存档码时，阿柜会根据已保存的互动回应；换一个存档码则成为新顾客。每轮有台词、行动后果和三个下一步选项，玩家也可以自由输入。

2026-11-06 已在 Ubuntu / Docker 的 Dify 1.13.0 中实际导入并发布，使用本机 Qwen3 1.7B 完成首轮、重启后回访、和解、新玩家四轮，以及浏览器网页互动。四轮各执行 9 个节点并成功保存，实际明细与审计见 [运行记录](evidence/RUN_RECORD.md)。数据隔离已核验；模型仍可能虚构旧事，详见已知限制。

快速本地部署请看 [部署说明](deploy/README.md)，无需云端模型额度。下面同时保留已有 Dify 实例手动接入配套服务的方法。

## 技术分工与交付状态

- **Dify Workflow**：接收行动，编排记忆读取 → LLM 剧情生成 → 记忆写入 → 确认后输出。模型在 Dify 中调用。
- **PowerContext**：通过真实 `open_builtin_runtime`、Scope、`memory.remember/list/get` 保存互动和读取精确版本。SQLite 是 PowerContext 的持久化后端；配套服务没有自行维护一份替代记忆数据库。
- 配套 HTTP 服务：把上述 SDK 接口提供给 Dify HTTP Request 节点。无需 seekdb、Embedding、Rerank 或自定义 Dify 插件。
- 已交付源码、DSL、本地部署脚本、后端自测、四轮真实 Dify 输出与节点记录、PowerContext 审计、服务重启记录和脱敏的 Dify 导出文件。

目录中的 `grudge-shop.yml` 是唯一需要导入的 DSL。`build_dsl.py` 可重新生成它；`node_code.py` 的四个函数已嵌入 DSL，Dify 无需读取本地 Python 文件。

## 版本与前置条件

- Python **3.12**，Git；本次实际验证在 WSL / Linux 完成。Windows 建议使用下述 WSL 部署；原生 Windows 的完整 SDK 测试尚未验证通过。
- 已验证 Dify **1.13.0** 自部署版，DSL **0.6.0**。其他版本请先检查兼容性。Dify 本体沿用官方 PostgreSQL 部署，不使用本项目的 SQLite 替换 Dify 数据库。
- PowerContext 固定源码提交 `9db1753434162c0aa7c9f1189a1722e617880a52`，见 `requirements.txt`。
- DSL 默认使用 `langgenius/openai_api_compatible/openai_api_compatible` 插件 **0.0.73** 和本机 `qwen3-1.7b`。可在 LLM 节点换成自己实际可用的聊天模型；不要求 MiniMax。模型文件版本、校验摘要及本机启动方式见部署说明。
- Dify worker 能访问记忆服务；Dify 模型供应商能访问对应模型 API。模型密钥仅配置在 Dify，不写进源码或提交日志。
- 配套服务使用 1 个进程，建议 512 MB 以上可用内存；Dify 本体按官方资源要求部署。`deploy/` 提供单独的本地 Dify 和 CPU 模型部署脚本。

## 1. 安装配套服务

在参赛仓库的 `dify/` 中执行：

```powershell
# Windows PowerShell
cd "C:\Users\MECHREVO\Documents\ChatGPT\oceanbase\powercontext-dify\dify"
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:NPC_API_TOKEN = "替换为自己生成的随机字符串"
.\.venv\Scripts\python.exe npc_service.py --host 0.0.0.0 --port 8790
```

Linux 服务器：

```bash
cd /path/to/team-repository/dify
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
export NPC_API_TOKEN='replace-with-your-own-random-token'
.venv/bin/python npc_service.py --host 0.0.0.0 --port 8790
```

`data/` 首次运行自动创建。数据库为 `data/npc-memory.db`；实际记忆读写与精确 citation 记录在 `data/powercontext-audit.jsonl`。不要同时启动多个服务进程写同一个数据库。`--data-dir` 可指定另一个可写目录。

另开终端访问 `http://127.0.0.1:8790/health`，应返回服务名和 `status: ok`。此健康检查仅证明服务已启动，不代表技术结合验收完成。

## 2. 导入 Dify DSL 并绑定模型

1. 自部署 Dify：按 [Dify 1.13.0 官方部署目录](https://github.com/langgenius/dify/tree/1.13.0/docker) 配置并启动，浏览器进入 Studio。
2. 选择从 DSL 文件导入，上传本目录的 **`grudge-shop.yml`**。只需导入这一个文件。
3. 在工作流环境变量中设置：

| 环境变量 | 填写内容 |
|---|---|
| `NPC_BASE_URL` | Dify worker 可以访问的服务地址，例如 `http://192.168.1.20:8790`，末尾不加 `/` |
| `NPC_API_TOKEN` | 与启动记忆服务时的值一致，类型为 Secret |

4. 打开“店长产生回响”LLM 节点，选择实际已配置且可调用的模型。默认 provider 标识是 `langgenius/openai_api_compatible/openai_api_compatible`。如导入时提示依赖不可用，先从 Marketplace 安装对应模型供应商插件，再重新选择模型。DSL 不携带供应商密钥或已安装插件包。
5. 确认两个 HTTP Request 节点分别请求 `/prepare`、`/commit`。请求体由 Code 节点 JSON 编码，玩家输入中的引号与换行可以保留。
6. 点击测试，按验证样例运行；成功后发布 Workflow，使用其 Web App 入口体验。

**网络说明**：Dify 在 Docker 内时，`127.0.0.1` 指 Dify 容器自身。Docker Desktop 可用 `host.docker.internal`；Linux Docker 可用实际宿主机地址，或在相应服务添加 `host-gateway` 映射。以 worker / SSRF proxy 能访问该地址为准。Dify Cloud 无法直接访问学校内网 IP；需要部署一条可访问且带 HTTPS 的服务地址，或使用学校服务器自部署 Dify。不要因为健康检查在自己浏览器可访问，就认为 Dify 容器也可访问。

## 3. 体验样例：恶作剧、重逢与和解

初始条件：服务已启动，新玩家存档码 `demo-bqh-001` 尚无记录。所有操作均是虚构游戏。

| 次序 | player_id | turn_id | action |
|---|---|---|---|
| 1 | demo-bqh-001 | t1 | 我把你的咖啡价签换成“学一声猫叫”，并自称猫叫大王。 |
| 2 | demo-bqh-001 | t2 | 我又回来了。你还记得我上次在这里干了什么吗？ |
| 3 | demo-bqh-001 | t3 | 我把咖啡价签恢复原样，认真道歉，再帮你整理货架。 |
| 4 | another-player-001 | t1 | 第一次来，你好，给我推荐一件奇怪的商品。 |

第 1 轮后，关闭记忆服务（Ctrl+C），使用相同命令、相同 data 目录重新启动，再运行第 2 轮。

预期：第 2 轮“提取历史”节点包含第 1 轮的实际行动；完整历史回复仍保存在 PowerContext 和 HTTP 读取响应中，但不再次作为模型续写正文，减少复述。店长回应应提及换价签、猫叫等旧互动。第 3 轮应对道歉和帮助产生新反馈。第 4 轮节点历史应为空。模型理想情况下只回应新客当前行动，但本机模型尚不能可靠遵守叙事约束，不能单靠其口头“记得”来判断真实存档。

每次成功输出包含：`reply`（剧情）、`memory_receipt`（真实 MemoryCitation JSON）、`saved_turns`（已保存轮数）。查看 Dify 节点执行记录，对照“PowerContext 读取记忆”和“PowerContext 保存互动”的响应，以及后端审计记录中的相同 citation。

重置方法：使用一个全新的 player_id，即可获得空白存档；无需删除数据库。相同 player_id 下每轮换 turn_id。同一 turn_id 的相同行动重复提交会返回原回复和原引用，不新增记忆；将同一 turn_id 用于另一个行动会报冲突。两个并行工作流都读到旧进度时，仅先完成的一轮写入，另一轮提示重新执行，避免覆盖历史。

## 4. 自测与实际运行证据

后端真实 PowerContext 自测（不需要模型 API）：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

该检查使用明确标注的固定台词，验证保存、重开 Runtime、精确引用、玩家隔离、重复提交和陈旧进度拒绝。不能替代 Dify 运行证据。

Dify 发布后，在应用 API 页面获取 Workflow API Key，另开 PowerShell：

```powershell
$env:DIFY_API_URL = "http://你的Dify地址/v1"
$env:DIFY_APP_KEY = "你的工作流API密钥"
.\.venv\Scripts\python.exe verify_dify.py --player review-demo-001 --phase first
# 重启记忆服务，然后继续；不要换 data 目录。
.\.venv\Scripts\python.exe verify_dify.py --player review-demo-001 --phase return
.\.venv\Scripts\python.exe verify_dify.py --player review-demo-001 --phase repair
```

Linux 同理使用 `export` 设置变量及 `.venv/bin/python`。脚本仅将实际运行结果写入 `evidence/live-*.json`，包含时间、DSL 摘要、workflow_run_id、输出与状态，不记录密钥。核对其中的模型回复及记忆凭据，再把脱敏后的真实日志作为提交材料加入版本管理；`.gitignore` 默认忽略它们，避免误上传运行数据。

此外保存 Dify 节点运行记录截图，填写 `evidence/RUN_RECORD.md` 中的实际版本及结果。不要把待填写内容改成未经执行的“已通过”。

## 已知限制与异常处理

- 已完成真实 Dify 导入、模型联调、四轮 API 验证和网页验证。运行证据中的模型文本保持原样，包括生成质量问题。
- 每个玩家保存完整历史，但一次只把最近 12 轮放进模型上下文；更早记录仍在 PowerContext 中，本版不承诺长程语义召回。
- 原型以玩家存档码划分 scope，持有码的人可继续该存档，不能当作正式用户身份认证。演示时使用随机存档码和虚构内容。
- 存档状态来自 PowerContext；剧情由模型生成。实测 Qwen3 1.7B 仍可能混淆角色、重复选项或编造旧事。新玩家样例实际读取 0 条历史，但模型虚构了其他外号，这属于生成幻觉，不能当作跨玩家记忆泄露的证据，也不能称为叙事约束已通过。节点明细保留了空历史，便于核对。更严格的剧情要求需要更强模型或额外的语义校验。
- 服务不可达或 token 错误时，读取节点后中止；写入失败时，不通过最终节点宣称已保存。LLM 失败不会新增记忆。进度冲突请重跑；回复超过服务上限时请在 LLM 节点减小输出长度并重试。
- 应用不预置外部数据，不需要知识库；选择本地方案时须下载并启动模型。不接触支付和真实店铺数据。玩法、提示词和配套代码为本项目编写；PowerContext、Dify、Qwen 和 llama.cpp 使用各自上游许可及来源。

接口和 DSL 字段参照 [Dify HTTP Request 文档](https://docs.dify.ai/en/cloud/use-dify/nodes/http-request)、[Dify 1.13.0 节点实现](https://github.com/langgenius/dify/tree/1.13.0/api/core/workflow/nodes)。较新的 Dify 版本可能使用不同导入界面，请优先使用本项目声明版本。
