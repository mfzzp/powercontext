# 实际运行记录（2026-11-06）

实际完成本机部署、导入发布、四轮 Dify API 运行、PowerContext 跨服务重启复用，以及网页运行。以下报告功能、数据和生成质量的实际边界。

## 固定环境

- 分支：`2026-competition-dify`；比赛基线 `d647275c308e09b7b623f322a26f5c4e85734fb0` 是当前分支祖先，Git 检查退出码 0。
- Ubuntu 24.04 / WSL 2，Docker 29.1.3，Compose 2.40.3，Python 3.12.3。
- Dify 1.13.0，官方源码 `c730fec1e422e907fed668291b2ea18c4c243644`；官方模型插件 OpenAI-API-compatible 0.0.73。
- 模型：`Qwen/Qwen3-1.7B-GGUF` Q8_0，版本和官方 SHA256 见 deploy/assets.json；llama.cpp b12144，本机 CPU 推理。
- PowerContext 1.2.0，固定源码 `9db1753434162c0aa7c9f1189a1722e617880a52`，实际 Scope 与 memory.remember/list/get，SQLite 持久化。
- Dify 应用：`e51c69a7-480b-4347-aeba-4399acc6179d`。
- 仓库 DSL SHA256：`51c06a1c73817cb088946b5a8298bf8a66e76843ee5e8aedeff196c05d9bf781`。

## 四轮实际结果

| 样例 | workflow_run_id | 状态 / 节点数 | 读取历史数 → 保存后轮数 | 单次耗时（秒） |
|---|---|---|---|---|
| 首次互动 | 8c7f8d75-6415-4a9d-84bd-d8ac6d7e0dce | succeeded / 9 | 0 → 1 | 13.498653 |
| 重启后回访 | 91aa7d50-f267-497d-b9d4-172a3d5d2829 | succeeded / 9 | 1 → 2 | 10.385667 |
| 恢复价签与道歉 | 7deb3bce-2d5d-4e29-a9e8-74973dfac6a6 | succeeded / 9 | 2 → 3 | 11.318276 |
| 新玩家数据隔离 | 0b1d651f-672e-40fd-b928-4636d7e305f8 | succeeded / 9 | 0 → 1 | 29.222199 |

以上为单次功能运行耗时，不能作为多次性能基准。各轮原始 JSON、全部节点输入输出、实际 Dify 导出 DSL 和 PowerContext 审计位于 [verified-20261106-17b](verified-20261106-17b/)。敏感凭据已移除，模型原文未改写。

## 记忆作用与服务重启

首轮存档 `submit-20261106-a` 的行动是将咖啡价签改成“学一声猫叫”，自称“猫叫大王”。回访输入只问是否记得上次行为。回访节点实际读到原行动，模型实际提及价签和猫叫。第三轮输入恢复价签、道歉和整理货架，模型回应“让我有点感动”，描述氛围缓和。

服务重启时间（UTC）：`2026-11-06T09:51:02.909476+00:00`。MainPID `50100 → 57183`，保持同一个数据目录；原始事件见 `system-restart.json`。`verification-summary.json` 和 `powercontext-audit.jsonl` 可核对旧玩家读到 1、2 条记录，以及读回的精确 citation。

## 新玩家结果及明确限制

新玩家 `submit-20261106-b` 的 HTTP 读取、提取历史及审计均为 0 条历史，Scope 与主玩家不同。这证明本样例的数据隔离。该轮模型却虚构了“小糊涂蛋”等未存档的旧事，因此**叙事的首次见面约束未能可靠满足**，不能称为模型严格遵守历史。此生成问题已保留在 isolation.json 和对应节点原文中。

本作品不把模型口头“记得”当成持久化证据；以 PowerContext 记录和节点输入为依据。模型还可能混淆角色或重复选项。已从上下文中移除旧模型回复，只复用真实玩家行动，减少照抄；此处理并不能完全消除幻觉。

## 网页与测试

浏览器实际填写新存档 `browser-submit-20261106`、轮次 `ui-first-1` 和“你好，第一次来。请推荐一件会吐槽的奇怪商品。”。页面显示全部 9 个节点、阿柜台词、本轮后果、A/B/C 选项、saved_turns=1，以及 entry_id `mem_ent_54585c2f25c84e34a26269ca01a1d12c` 的真实凭据。修复了后台队列未同步随机 Redis 密码导致的网页持续加载问题。

最终源码的 Linux 单测实际执行：`python -m unittest discover -s tests -v`，2 个测试通过，0.746 秒，退出码 0。覆盖重开 Runtime、玩家隔离、幂等、过期写入拒绝、Code 节点序列化和历史只复用行动。原生 Windows SDK 全量测试没有验证通过，支持环境以 WSL / Linux 为准。

## 复现入口

按 [部署说明](../deploy/README.md) 从干净环境安装、下载模型、导入 DSL、执行新存档四轮样例。私有凭据在部署目录 permissions 600 的 credentials.json 中，不在仓库。每次使用新玩家码即可恢复初始状态。

本机入口为 `http://127.0.0.1:18080/workflow/8jY8KvBNmGngdbl8`；评审自己部署时，由 configure_dify.py 输出其自己的入口。提交不依赖作者电脑在线。
