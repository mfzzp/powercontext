# 复习接力站 - 运行说明

## 依赖

- Python 3.12+
- 依赖包见 `requirements.txt`

```text
powercontext[builtin] @ git+https://github.com/oceanbase/powercontext.git@9db1753434162c0aa7c9f1189a1722e617880a52
aiohttp>=3.9,<4
pytest>=8
pytest-asyncio>=0.23
aiofiles>=23
```

## 安装

```bash
cd qoder
pip install -r requirements.txt
```

## 启动

```bash
# 默认 127.0.0.1:8080，数据目录 ./data/
python -m app.main

# 自定义端口和数据目录
REVIEW_PORT=9090 REVIEW_DATA_DIR=./my-data python -m app.main
```

启动后浏览器访问 http://127.0.0.1:8080

## 端口与配置

| 环境变量 | 默认值 | 说明 |
|---|---|---|
| REVIEW_HOST | 127.0.0.1 | 监听地址 |
| REVIEW_PORT | 8080 | 监听端口 |
| REVIEW_DATA_DIR | ./data | 数据存储目录（含 SQLite 和审计日志） |

## 运行测试

```bash
cd qoder
python -m pytest tests/test_app.py -v
```

测试覆盖：
- 样例 A：薄弱点与跨启动接力
- 样例 B：隔离与幂等、参数校验
- 样例 C：端到端与错误反馈
- 推荐排序优先级
- 跨重启持久化
- 掌握数去重
- 失败不虚报成功

## 重置

每个存档码对应独立的学习进度。要重置某用户的数据：
1. 使用新的存档码即可开始全新进度
2. 若要彻底清除所有数据，删除 `data/` 目录后重启服务

**注意**：不要默认删除用户目录，应通过切换存档码实现"重置"。

## 已知限制

- 单进程写者模型，通过 threading.Lock 保证并发安全
- 不依赖外部模型 API，题库为固定原创内容
- 存档码仅作原型隔离，非正式身份认证
- 前端未做服务端渲染，需要 JavaScript 支持

## 技术栈

- 后端：Python 3.12 + aiohttp
- 前端：原生 HTML/CSS/JS
- 持久化：PowerContext SQLite 后端
- 测试：pytest + pytest-asyncio

## 实际运行记录

### 环境
- Python 3.12.10
- Windows 10
- PowerContext @ 9db17534

### 测试结果
```
8 passed in 5.24s
```

### 启动命令
```bash
cd qoder
python -m app.main
```
退出码：0（正常启动监听）
