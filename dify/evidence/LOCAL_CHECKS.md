# 本地已执行检查（2026-11-04）

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
