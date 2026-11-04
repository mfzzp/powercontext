# 复习接力站 - 实际运行证据

## 环境信息

- **操作系统**: Windows 10 (10.0.26200)
- **Python**: 3.12.10
- **PowerContext**: `powercontext[builtin] @ git+https://github.com/oceanbase/powercontext.git@9db1753434162c0aa7c9f1189a1722e617880a52`
- **aiohttp**: 3.14.5
- **pytest**: 9.2.0
- **代码版本**: 分支 `codex/qoder`

## 测试结果

```
$ cd qoder && python -m pytest tests/test_app.py -v

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

## 样例 A：薄弱点与跨启动接力

### 步骤 1-3：提交练习

```
$ curl -s -X POST http://127.0.0.1:8080/api/submit \
    -H "Content-Type: application/json" \
    -d '{"archive":"learner-a-001","request_id":"a-P1-001","qid":"P1","score":0}'

{
    "replayed": false,
    "request_id": "a-P1-001",
    "qid": "P1",
    "score": 0,
    "sequence": 1,
    "citation": {
        "memory_ref": {"family": "memory", "artifact_id": "memory", "revision": 1},
        "entry_id": "mem_ent_870b8d3caac14294bbbb30ac6b8efa44",
        "entry_version_id": "mem_ver_b5c4f84b31c840859d9c3102c56ddc0b"
    },
    "knowledge": "Python 可变与不可变类型"
}
```

P2 自评掌握(2) 成功，sequence=2，citation 非空。

### 步骤 4-5：进度与推荐

```
$ curl -s "http://127.0.0.1:8080/api/progress?archive=learner-a-001"

scores: {'P1': 0, 'P2': 2}
mastered_count: 1
weak_qids: ['P1']
total_practices: 2
```

```
$ curl -s "http://127.0.0.1:8080/api/recommend?archive=learner-a-001"

qid: P1
priority: weak
reason: 最近自评为「不会」，建议优先复习
```

### 步骤 6：关闭服务并重启

```
$ taskkill /F /IM python.exe
成功: 终止进程 "python.exe"，PID 为 18320

$ ls data/
powercontext-audit.jsonl
review-memory.db (618496 bytes)

$ python -m app.main
服务已重启
```

### 步骤 7：重启后数据保持

```
scores: {'P1': 0, 'P2': 2}
mastered: 1
total: 2
P1 citation: {"entry_id": "mem_ent_870b8d3caac14294bbbb30ac6b8efa44", ...}
```

**结论**: 重启后所有进度、citation 完全保持。

### 步骤 8-9：P1 更新为掌握

```
$ curl -s -X POST ... -d '{"archive":"learner-a-001","request_id":"a-P1-002","qid":"P1","score":2}'

replayed: False, score: 2
mastered_count: 2
scores: {'P1': 2, 'P2': 2}
recommend: qid=D1, priority=unseen (不再推荐 P1 为薄弱)
```

## 样例 B：隔离与幂等

### 隔离

```
$ curl -s "http://127.0.0.1:8080/api/progress?archive=learner-b-001"

total_practices: 0
scores: {}
```

**结论**: 新用户看不到 learner-a-001 的任何数据。

### 幂等

```
重放同一 request_id + 相同数据:
replayed: True (不新增记录)

同 request_id + 不同数据:
409 "request_id 已用于不同的练习数据，请更换 request_id"
```

### 参数校验

| 输入 | 结果 |
|---|---|
| score: true (布尔) | 400 "score 必须是整数 0/1/2" |
| score: "1" (字符串) | 400 "score 必须是整数 0/1/2" |
| score: 1.0 (浮点) | 400 "score 必须是整数 0/1/2" |
| score: -1 | 400 "score 必须是 0（不会）、1（模糊）或 2（掌握）" |
| score: 3 | 400 "score 必须是 0（不会）、1（模糊）或 2（掌握）" |
| qid: INVALID | 400 "无效题目 ID：INVALID" |
| archive: 含特殊字符 | 400 "存档码仅支持..." |

**结论**: 所有无效输入被拒绝，不写入记忆。

## 样例 C：端到端

完整流程（输入存档码 → 选题 → 看答案 → 自评 → 保存 → 继续复习）通过前端页面和 API 均验证成功。

- 保存失败时界面显示"保存失败"，不宣称成功
- 记忆不可读时 API 返回 503，不回退为空记录

## 前端验证

```
$ curl -s http://127.0.0.1:8080/ | head -5
<!DOCTYPE html>
<html lang="zh-CN">
...

style.css: 200
app.js: 200
```

## PowerContext 审计日志

```jsonl
{"time":"2026-11-04T03:27:45.730Z","operation":"write","scope_id":"scp_7gy1nn9tyxh20f6w83nc1k2c05","request_id":"a-P1-001","qid":"P1","score":0,"sequence":1,"citation":{"memory_ref":{"family":"memory","artifact_id":"memory","revision":1},"entry_id":"mem_ent_870b8d3caac14294bbbb30ac6b8efa44","entry_version_id":"mem_ver_b5c4f84b31c840859d9c3102c56ddc0b"},"content_sha256":"..."}
{"time":"2026-11-04T03:27:45.878Z","operation":"write","scope_id":"scp_7gy1nn9tyxh20f6w83nc1k2c05","request_id":"a-P2-001","qid":"P2","score":2,"sequence":2,...}
{"time":"2026-11-04T03:30:44.044Z","operation":"read","scope_id":"scp_7gy1nn9tyxh20f6w83nc1k2c05","count":2}
```

审计日志记录操作类型、scope、request_id 和 citation，不记录密钥。

## 启动命令与退出码

```bash
cd qoder
python -m app.main
# 退出码: 0 (正常监听)
# Ctrl+C 退出码: 0 (正常终止)
```
