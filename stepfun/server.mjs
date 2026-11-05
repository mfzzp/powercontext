import express from 'express';
import cors from 'cors';
import { SeekDBClient } from 'seekdb';

const PORT = Number(process.env.PORT || 8787);
const DB = process.env.SEEKDB_DATABASE || 'stepfun_kb';
const COLLECTION = process.env.SEEKDB_COLLECTION || 'documents';
const app = express();
app.use(cors());
app.use(express.json({ limit: '20mb' }));

let client;
let collection;

async function init() {
  client = new SeekDBClient({
    host: process.env.SEEKDB_HOST || '127.0.0.1',
    port: Number(process.env.SEEKDB_PORT || 2881),
    user: process.env.SEEKDB_USER || 'root',
    password: process.env.SEEKDB_PASSWORD || '',
    database: DB,
  });
  collection = await client.getOrCreateCollection({
    name: COLLECTION,
    embeddingFunction: null,
    configuration: { hnsw: { space: 'cosine' } },
  });
}

app.get('/api/status', async (_req, res) => {
  try {
    const count = await collection.count();
    res.json({ ok: true, database: DB, collection: COLLECTION, count });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

app.post('/api/documents', async (req, res) => {
  try {
    const docs = req.body?.documents || [];
    if (!Array.isArray(docs) || docs.length === 0) {
      return res.status(400).json({ error: 'documents must be a non-empty array' });
    }
    const ids = docs.map((d) => String(d.id));
    const embeddings = docs.map((d) => d.embedding);
    const documents = docs.map((d) => String(d.text));
    const metadatas = docs.map((d) => ({ source: d.source || 'local', ...(d.metadata || {}) }));
    await collection.add({ ids, embeddings, documents, metadatas });
    res.json({ ok: true, added: docs.length });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

app.post('/api/search', async (req, res) => {
  try {
    const { embedding, queryText, nResults = 5 } = req.body || {};
    if (!Array.isArray(embedding) || !queryText) {
      return res.status(400).json({ error: 'embedding and queryText are required' });
    }
    const result = await collection.hybridSearch({
      query: { whereDocument: { $contains: String(queryText) }, nResults: 20 },
      knn: { queryEmbeddings: [embedding], nResults: 20 },
      rank: { rrf: {} },
      nResults: Number(nResults),
    });
    res.json({ ok: true, result });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

app.delete('/api/documents', async (_req, res) => {
  try {
    const ids = (await collection.get()).ids;
    if (ids?.length) await collection.delete({ ids });
    res.json({ ok: true, deleted: ids?.length || 0 });
  } catch (e) {
    res.status(500).json({ ok: false, error: String(e.message || e) });
  }
});

init()
  .then(() => app.listen(PORT, '127.0.0.1', () => console.log(`StepFun API on http://127.0.0.1:${PORT}`)))
  .catch((e) => { console.error('seekdb init failed:', e); process.exit(1); });
