# Dify 赛题提交索引

作品：记仇便利店——阿柜真的记得你。

| 提交项 | 内容 |
|---|---|
| 仓库 | https://github.com/mfzzp/powercontext |
| 分支 | 2026-competition-dify |
| 固定提交 | 使用本分支最终 `git rev-parse HEAD` 的完整输出，填写到测评平台 |
| DSL | grudge-shop.yml，唯一入口文件，无需依次导入多个应用 |
| 运行说明 | README.md；完整本地部署见 deploy/README.md |
| 运行证据 | evidence/verified-20261106-17b/ 与 evidence/RUN_RECORD.md |
| 模型来源、版本和校验 | deploy/assets.json；Qwen 官方公开模型，本机 CPU 推理 |
| 指定技术 | Dify 核心编排 + PowerContext 记忆保存与跨重启复用 |

实际完成首轮、重启后回访、和解、新玩家四轮，每轮全部 9 个节点 succeeded。新玩家的读取结果为 0，旧玩家读取 1、2 条已有互动；对应节点响应、精确 citation 和审计均已交付。

网页入口在本地部署脚本输出的 `deployment.json` 中。本题提供可本地复现的应用，不依赖作者电脑在线，也不需要作者的管理员密码、云端模型 Key 或既有数据库。

已知质量限制：模型可能混淆角色或虚构旧事；新玩家样例中实际出现了无来源的旧事。数据隔离与模型叙事遵从是两项不同的验证，不能把后者称为已可靠通过。全部原始输出保留，未使用固定剧情替换模型。
