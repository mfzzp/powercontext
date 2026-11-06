# 复习接力站：Qoder 开发任务包

## 当前状态

**主要功能已完成并通过全部验收测试。** 代码由 Qoder 在 `2026-competition-qoder` 分支实际开发，真实 PowerContext 保存、重启持久化、幂等提交、参数校验均已验证。展示网站通过 Qoder Sites 发布。

## 已完成内容

| 项目 | 状态 | 说明 |
|---|---|---|
| 后端 API（aiohttp） | 已完成 | 存档码、选题、自评、推荐、进度查询 |
| 前端界面（HTML/CSS/JS） | 已完成 | 中文、适配桌面手机、空状态、失败提示 |
| 原创题库 | 已完成 | Python / Git / 数据库各 5 题，共 15 题 |
| PowerContext 持久化 | 已完成 | 真实 SQLite 后端，memory.remember / list / get |
| 幂等与隔离 | 已完成 | 同 ID 重试幂等，不同 ID 冲突 409，跨用户隔离 |
| 自动测试 | 已完成 | 8 项全部通过，覆盖 ACCEPTANCE.md 全部场景 |
| 跨重启持久化 | 已验证 | 关闭服务重启后进度完整保持 |
| 展示网站 | 已完成 | site/index.html，通过 Qoder Sites 发布 |

## 快速开始

```bash
# 克隆仓库
git clone https://github.com/mfzzp/powercontext.git
cd powercontext
git checkout 2026-competition-qoder
cd qoder

# 安装依赖（Python 3.12+）
pip install -r requirements.txt

# 启动服务
python -m app.main

# 浏览器访问
# http://127.0.0.1:8080

# 运行测试
python -m pytest tests/test_app.py -v
```

## 目录结构

```
qoder/
  app/
    __init__.py
    main.py            # aiohttp 后端入口 + API 路由 + 静态文件服务
    memory_service.py  # PowerContext 封装：scope、写入、读取、推荐
    questions.py       # 原创题库（Python/Git/数据库各 5 题）
    static/
      index.html       # 前端页面
      style.css        # 样式
      app.js           # 前端逻辑
  tests/
    test_app.py        # 8 项验收测试
  site/
    index.html         # 展示网站（通过 Qoder Sites 发布）
  evidence/
    RUN_LOG.md         # 实际运行证据
  requirements.txt
  RUN.md               # 运行说明
  ACCEPTANCE.md        # 验收样例
  QODER_PROMPT.md      # 需求文档
  README.md            # 本文件
  SUBMISSION.md        # 参赛材料登记
```

## 技术栈

- **后端**：Python 3.12 + aiohttp 3.14
- **前端**：原生 HTML / CSS / JavaScript
- **持久化**：PowerContext SQLite 后端（固定版本 `9db17534`）
- **测试**：pytest 9 + pytest-asyncio
- **不依赖**：外部模型 API、Embedding、seekdb

## PowerContext 使用

每个存档码对应独立 scope，`idempotency_key` 为 `review-station/{archive_code}`。

- 写入：`memory.remember(RememberMemoryRequest(entries=(MemoryEntryInput(kind="review-practice", text=...),)))`
- 查询：`memory.list()` 返回原记录，`memory.get(GetMemoryEntryRequest(citation=...))` 精确回读
- 审计日志：`data/powercontext-audit.jsonl`，记录操作类型、scope、request_id、citation，不记录密钥

## 验收结果摘要

```
tests/test_app.py::test_a_weak_points_and_restart PASSED
tests/test_app.py::test_b_isolation_and_idempotent PASSED
tests/test_app.py::test_b_validation PASSED
tests/test_app.py::test_c_end_to_end PASSED
tests/test_app.py::test_recommendation_priority PASSED
tests/test_app.py::test_restart_persistence PASSED
tests/test_app.py::test_mastered_count_dedup PASSED
tests/test_app.py::test_failure_not_reported_as_success PASSED

8 passed in 5.24s
```

详细运行证据见 `evidence/RUN_LOG.md`。

## 重置与限制

- **重置**：使用新存档码即可开始全新进度；彻底清除需删除 `data/` 目录
- **单进程写者**：通过 `threading.Lock` 保证并发安全
- **存档码**：仅作原型隔离，非正式身份认证
- **不收集**：用户真实身份信息
