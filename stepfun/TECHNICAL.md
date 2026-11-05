# 技术说明：seekdb × StepFun 端侧迷你知识库

## 模块结构

```text
Browser
  ├─ Transformers.js pipeline (all-MiniLM-L6-v2, q8)
  │    ├─ WebGPU 可用：device=webgpu
  │    └─ 否则：device=wasm，并显示降级提示
  ├─ 文档/查询向量化（全部在用户设备）
  └─ fetch /api
        │
Node server
  ├─ Express API
  └─ seekdb-js SeekDBClient(server mode)
        └─ collection: documents
             ├─ embedding VECTOR(384)
             ├─ documents TEXT
             └─ metadata JSON
```

## 数据流

1. 用户粘贴文本，每行一条；
2. 浏览器按 batch=8 调用 `feature-extraction`，`pooling=mean, normalize=true`；
3. 向量、原文、metadata 通过 `POST /api/documents` 写入 seekdb；
4. 查询文本在同一浏览器环境编码；
5. `POST /api/search` 调用 `collection.hybridSearch`：
   - `query.whereDocument.$contains`：全文条件；
   - `knn.queryEmbeddings`：向量条件；
   - `rank.rrf`：seekdb 内部 RRF 融合；
6. 返回 ids/documents/metadatas，页面按排序展示。

## 量化

- 模型：`Xenova/all-MiniLM-L6-v2`
- 维度：384
- dtype：`q8`（INT8）
- 模型由 Transformers.js 下载并缓存；实际推理在浏览器内完成，不调用云端推理 API。

## WebGPU / WASM

```js
backend = navigator.gpu ? 'webgpu' : 'wasm';
```

WebGPU 初始化失败时捕获异常并重新以 `wasm` 初始化，界面显示后端和降级原因。

## seekdb 使用

- 模式：server mode（`SEEKDB_HOST/PORT/USER/PASSWORD/DATABASE`）
- 集合：`documents`
- 写入：`collection.add({ ids, embeddings, documents, metadatas })`
- 混合检索：`collection.hybridSearch(...)`
- 状态：集合和数据由 seekdb 持久化，刷新页面后继续使用。

## 性能记录

页面记录：

- 模型加载耗时；
- 文档 embedding 总耗时；
- 检索耗时；
- 文档数量；
- 当前推理后端。

## 限制与后续

- 当前未实现图片知识库；
- 量化体积/Recall@K 对比需要固定评测集；
- 浏览器 WebGPU 需在实际 Chrome/Edge 中验证；
- seekdb 需先启动，server 模式避免 Windows 下 embedded 原生绑定问题。
