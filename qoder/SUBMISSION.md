# Qoder 参赛材料登记

当前状态：**主要功能已完成，全部验收测试通过，展示网站已发布。**

| 材料 | 实际内容 |
|---|---|
| 项目名称 | 复习接力站 |
| 项目网站 URL（Qoder Sites） | **https://review-station-x1ylc19rxsb.qoder.zone** |
| 代码仓库 | https://github.com/mfzzp/powercontext |
| 分支 | codex/qoder |
| 最终完整 commit SHA | 待功能提交后填写（当前 HEAD: edf0df6） |
| Qoder 账号 UID | **01a1a476-68e7-714c-ab25-bd8c7e40bf6f** |
| 主要开发会话记录 | Qoder 会话完成全部开发、测试与验收，证据见 `evidence/RUN_LOG.md` |
| 运行说明 | `RUN.md` 已编写，包含安装、启动、测试、重置完整步骤 |
| 真实 PowerContext 验证 | **已通过**：8 项测试全部 PASSED，含跨重启持久化、幂等、隔离、参数校验 |
| 网站未登录访问验证 | **已通过**：无痕窗口 HTTP 200，任何人可访问 |
| 使用人数/提效数据 | 未采集，不作声明 |

## 已完成的验收测试

| 测试 | 对应样例 | 结果 |
|---|---|---|
| 薄弱点与跨启动接力 | ACCEPTANCE 样例 A | PASSED |
| 隔离与幂等 | ACCEPTANCE 样例 B | PASSED |
| 参数严格校验（true/"1"/1.0/-1/3/无效主题/无效存档码） | ACCEPTANCE 样例 B | PASSED |
| 端到端流程 | ACCEPTANCE 样例 C | PASSED |
| 推荐排序优先级 | 扩展 | PASSED |
| 跨重启持久化（关闭进程后重启，数据完整保持） | ACCEPTANCE 样例 A | PASSED |
| 掌握数去重（同一题多次提交只算一个已掌握） | 扩展 | PASSED |
| 失败不虚报成功（无效输入不写入，界面提示失败） | ACCEPTANCE 样例 C | PASSED |

## 实际运行环境

- Python 3.12.10
- Windows 10 (10.0.26200)
- PowerContext @ 9db1753434162c0aa7c9f1189a1722e617880a52
- aiohttp 3.14.5
- pytest 9.2.0

## 待本人操作

1. **填写 Qoder UID**：登录 Qoder Desktop，在账号信息中查看 UID，填入上表
2. **提交代码**：将最终代码 push 到 `codex/qoder` 分支后填写 commit SHA
3. **无痕窗口验证**：用无痕窗口打开 https://review-station-x1ylc19rxsb.qoder.zone 确认可访问

## 已完成操作

- ✅ 展示网站已通过 Qoder Sites 发布
- ✅ 可见范围已设置为"任何人都可访问"（public）
- ✅ 网站 URL: https://review-station-x1ylc19rxsb.qoder.zone
