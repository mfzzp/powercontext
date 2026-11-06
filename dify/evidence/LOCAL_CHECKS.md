# 历史本地检查（2026-11-04）

2026-11-06 新增：已在 WSL / Ubuntu 的实际 Dify 1.13.0 中完成导入发布、四轮 API 和网页互动，见 [RUN_RECORD.md](RUN_RECORD.md) 和 `verified-20261106-17b/`。四轮均 succeeded、9 步，且记录的 DSL 摘要与当前文件匹配；实际节点、审计和重启证据已导出。数据隔离已核验，模型叙事的首次见面约束仍有缺陷，未标为通过。

最终源码在 Linux 运行后端与工作流的两组单测，均通过，0.746 秒，退出码 0。额外的 Windows SDK 全量单测未在限定等待内结束，已中断，不能记为通过；本次运行环境以 WSL / Linux 为准。

以下保留 11 月 4 日的历史检查和当时边界，不代表当前最终 DSL 的摘要。

执行目录：参赛仓库根目录。解释器：已有的 Python 3.12 虚拟环境。
本地 `PYTHONPATH` 指向 `../powercontext-team/src` 和 `dify/`；PowerContext 本地源码版本为
`4fd793bbadf2135fed299bcf6e690b39a62f4ae3`。依赖安装说明固定官方冻结版本，尚需在干净安装环境再复现。

实际命令：

```text
python dify/build_dsl.py
python -m py_compile dify/npc_service.py dify/node_code.py dify/build_dsl.py dify/verify_dify.py dify/tests/test_memory.py
python -m unittest discover -s dify/tests -v
```

实际测试结果：

```text
test_restart_isolation_replay_and_stale_write ... ok
test_serialized_nodes_and_unicode_payload ... ok
Ran 2 tests in 3.402s
OK
exit code: 0
```

记忆测试真实调用 PowerContext，使用临时 SQLite 数据库。覆盖关闭/重新打开 Runtime 后读取、精确引用、重复请求不新增记忆、玩家隔离、陈旧进度与历史改写拒绝。
工作流检查解析 DSL，并编译其全部 Code 节点，检查带引号及换行的中文输入 JSON 编码、HTTP 失败不输出成功。

校验时文件 SHA-256：

```text
grudge-shop.yml: da28e6e30df9d0422bcb3552ab1ad8b9f57f7a1f150dd5c58e7819a743a23f08
npc_service.py: be541a1b7ca438d6140ab8cdc5c37f167347081ed72a062e5222394110652405
```

未执行：Dify 实例导入、模型供应商调用、已发布 Dify Workflow 的完整体验验证。这份记录不能代替这些运行证据；请按 README 执行并补全 RUN_RECORD.md。
