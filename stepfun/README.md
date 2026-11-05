# seekdb × StepFun 端侧迷你知识库

浏览器本地 embedding + seekdb 向量/全文混合检索。

## 结构

```text
stepfun/
  server.mjs        # Node/Express + seekdb-js，server 模式
  index.html        # Vite 页面
  src/main.js       # Transformers.js 本地推理、导入、检索
  src/style.css
  data/corpus.txt   # 示例语料（每行一条）
```

## 环境

- Node.js >= 20
- pnpm 或 npm
- seekdb server，默认 `127.0.0.1:2881`，root 空密码
- Chrome / Edge 最新版

## 启动

1. 启动 seekdb（任意已有实例均可），确认 MySQL 协议端口：

```bash
# 示例：Windows 已编译二进制
build_release/src/observer/seekdb.exe --base-dir D:/seekdb-data --port 2881 --nodaemon
```

2. 安装依赖：

```bash
cd stepfun
pnpm install
```

3. 启动后端：

```bash
# 使用已有 test 库；也可先创建 stepfun_kb 并改 SEEKDB_DATABASE
SEEKDB_HOST=127.0.0.1 SEEKDB_PORT=2881 SEEKDB_DATABASE=test \
SEEKDB_USER=root SEEKDB_PASSWORD= PORT=8787 node server.mjs
```

4. 启动前端：

```bash
node node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173
```

5. 打开 `http://127.0.0.1:5173`。

也可以在 PowerShell 中：

```powershell
$env:SEEKDB_DATABASE="test"
$env:PORT="8787"
node server.mjs
```

另开终端：

```powershell
node node_modules\vite\bin\vite.js --host 127.0.0.1 --port 5173
```

## 使用

1. 在文本框每行粘贴一条文档；
2. 点击“导入并向量化”；
3. 输入查询，点击“混合检索”；
4. 页面显示推理后端、seekdb 连接状态、结果和性能记录。

## 技术说明

- 模型：`Xenova/all-MiniLM-L6-v2`，384 维；
- 量化：Transformers.js `dtype: "q8"`（INT8）；
- 推理位置：浏览器 Web Worker/主线程内，文档和查询 embedding 均在本地计算；
- 后端：优先 `webgpu`，不可用自动降级 `wasm`，页面明确显示；
- seekdb：`SeekDBClient` server 模式，集合 `documents`；
- 混合检索：`collection.hybridSearch({ query: { whereDocument: { $contains } }, knn: { queryEmbeddings }, rank: { rrf: {} } })`，由 seekdb 完成，不在前端拼两路结果；
- 写入：文档向量、原文、metadata 一并写入 seekdb；
- 状态：集合由 seekdb 持久化，刷新页面后可继续检索。

## 量化与性能

当前实现使用 INT8 q8 模型。模型文件由 Transformers.js 从 Hugging Face 缓存下载；首次加载时间与后续请求分开记录在页面“性能记录”中。

量化前后体积/压缩比与 Recall@K 需要按同一评测集补充实测数据。当前页面记录：

```json
{
  "backend": "wasm",
  "model": "Xenova/all-MiniLM-L6-v2",
  "dtype": "q8",
  "documents": 0,
  "modelLoadMs": 0,
  "embeddingTotalMs": 0,
  "searchMs": 0
}
```

## 已知限制

- 本机 Windows 未安装 Chrome 命令行入口，浏览器端 WebGPU/WASM 路径需在 Chrome/Edge 中手工验证；
- 图片知识库为扩展项，当前只实现文本；
- seekdb 需提前启动；embedded 模式依赖平台原生绑定，当前采用 server 模式；
- 量化质量对比需要补充固定评测集与 Recall@K 脚本。
