import { pipeline, env } from '@huggingface/transformers';
import './style.css';

env.allowLocalModels = false;
const API = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8787';
const MODEL = 'Xenova/all-MiniLM-L6-v2';
const $ = (id) => document.getElementById(id);

let extractor = null;
let backend = 'wasm';
let docs = [];
let metrics = { loadMs: 0, embedMs: 0, searchMs: 0, docs: 0 };

function setText(id, text) { $(id).textContent = text; }
function now() { return performance.now(); }

async function initModel() {
  const hasWebGPU = !!navigator.gpu;
  backend = hasWebGPU ? 'webgpu' : 'wasm';
  setText('backend', `${backend}${hasWebGPU ? '' : '（自动降级，WebGPU 不可用）'}`);
  const t0 = now();
  try {
    extractor = await pipeline('feature-extraction', MODEL, {
      device: backend,
      dtype: 'q8',
    });
  } catch (e) {
    backend = 'wasm';
    setText('backend', `wasm（WebGPU 初始化失败，已降级：${String(e.message || e)}）`);
    extractor = await pipeline('feature-extraction', MODEL, { device: 'wasm', dtype: 'q8' });
  }
  metrics.loadMs = Math.round(now() - t0);
  renderMetrics();
}

async function embed(texts) {
  const t0 = now();
  const output = await extractor(texts, { pooling: 'mean', normalize: true });
  const dim = output.dims[output.dims.length - 1];
  const data = Array.from(output.data);
  const vectors = [];
  for (let i = 0; i < texts.length; i++) vectors.push(data.slice(i * dim, (i + 1) * dim));
  metrics.embedMs += Math.round(now() - t0);
  return vectors;
}

function renderMetrics() {
  setText('metrics', JSON.stringify({
    backend,
    model: MODEL,
    dtype: 'q8',
    documents: metrics.docs,
    modelLoadMs: metrics.loadMs,
    embeddingTotalMs: metrics.embedMs,
    searchMs: metrics.searchMs,
  }, null, 2));
}

async function refreshStatus() {
  try {
    const r = await fetch(`${API}/api/status`);
    const j = await r.json();
    setText('db', j.ok ? `已连接 · ${j.database}.${j.collection} · ${j.count} 条` : `错误：${j.error}`);
  } catch (e) {
    setText('db', `连接失败：${String(e.message || e)}`);
  }
}

$('import').addEventListener('click', async () => {
  const lines = $('docs').value.split('\n').map((s) => s.trim()).filter(Boolean);
  if (!lines.length) return;
  $('import').disabled = true;
  try {
    setText('import-status', `本地向量化 0/${lines.length}…`);
    const chunks = [];
    for (let i = 0; i < lines.length; i += 8) {
      const batch = lines.slice(i, i + 8);
      const vectors = await embed(batch);
      batch.forEach((text, j) => chunks.push({
        id: `doc-${Date.now()}-${i + j}`,
        text,
        embedding: vectors[j],
        metadata: { importedAt: new Date().toISOString() },
      }));
      setText('import-status', `本地向量化 ${Math.min(i + 8, lines.length)}/${lines.length}…`);
    }
    const r = await fetch(`${API}/api/documents`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ documents: chunks }),
    });
    const j = await r.json();
    if (!j.ok) throw new Error(j.error);
    metrics.docs += j.added;
    docs.push(...chunks);
    setText('import-status', `已写入 seekdb ${j.added} 条`);
    renderMetrics();
    await refreshStatus();
  } catch (e) {
    setText('import-status', `失败：${String(e.message || e)}`);
  } finally {
    $('import').disabled = false;
  }
});

$('search').addEventListener('click', async () => {
  const q = $('query').value.trim();
  if (!q) return;
  $('search').disabled = true;
  try {
    const [vector] = await embed([q]);
    const t0 = now();
    const r = await fetch(`${API}/api/search`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ embedding: vector, queryText: q, nResults: 5 }),
    });
    const j = await r.json();
    metrics.searchMs = Math.round(now() - t0);
    if (!j.ok) throw new Error(j.error);
    const ids = j.result.ids?.[0] || [];
    const texts = j.result.documents?.[0] || [];
    $('results').innerHTML = ids.map((id, i) =>
      `<li><strong>${id}</strong><p>${texts[i] || ''}</p></li>`).join('');
    renderMetrics();
  } catch (e) {
    $('results').innerHTML = `<li class="error">检索失败：${String(e.message || e)}</li>`;
  } finally {
    $('search').disabled = false;
  }
});

$('clear').addEventListener('click', async () => {
  await fetch(`${API}/api/documents`, { method: 'DELETE' });
  docs = [];
  metrics.docs = 0;
  renderMetrics();
  await refreshStatus();
});

refreshStatus();
initModel().catch((e) => setText('backend', `模型加载失败：${String(e.message || e)}`));
