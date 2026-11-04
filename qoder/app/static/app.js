const API = "";
let currentArchive = "";
let currentTopic = "";
let currentQid = "";
let topicsData = {};

function $(id) { return document.getElementById(id); }

function setStatus(el, msg, type) {
  el.textContent = msg;
  el.className = "status" + (type ? " " + type : "");
}

async function apiGet(path) {
  const resp = await fetch(API + path);
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || "请求失败");
  return data;
}

async function apiPost(path, body) {
  const resp = await fetch(API + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await resp.json();
  if (!resp.ok) throw new Error(data.error || "保存失败");
  return data;
}

async function setArchive() {
  const input = $("archive-input");
  const status = $("archive-status");
  const code = input.value.trim();
  if (!code) {
    setStatus(status, "请输入存档码", "error");
    return;
  }
  if (!/^[a-zA-Z0-9_-]{1,64}$/.test(code)) {
    setStatus(status, "存档码仅支持字母、数字、下划线和连字符，1-64 位", "error");
    return;
  }
  currentArchive = code;
  setStatus(status, "存档码：" + code, "success");
  $("topic-section").style.display = "";
  $("continue-section").style.display = "none";
  $("question-section").style.display = "none";
  await loadTopics();
}

function resetArchive() {
  currentArchive = "";
  $("archive-input").value = "";
  $("archive-status").textContent = "";
  $("topic-section").style.display = "none";
  $("question-section").style.display = "none";
  $("continue-section").style.display = "none";
}

async function loadTopics() {
  try {
    const data = await apiGet("/api/topics");
    topicsData = data.overviews;
    const container = $("topic-list");
    container.innerHTML = "";
    for (const topic of data.topics) {
      const info = topicsData[topic];
      const div = document.createElement("div");
      div.className = "topic-btn";
      div.innerHTML = topicLabel(topic) + '<span class="count">' + info.count + " 题</span>";
      div.onclick = () => selectTopic(topic);
      container.appendChild(div);
    }
  } catch (e) {
    alert("加载主题失败：" + e.message);
  }
}

function topicLabel(t) {
  return { python: "Python", git: "Git", database: "数据库" }[t] || t;
}

async function selectTopic(topic) {
  currentTopic = topic;
  const info = topicsData[topic];
  if (!info || !info.questions.length) {
    alert("该主题暂无题目");
    return;
  }
  const q = info.questions[0];
  await loadQuestion(q.qid);
}

async function loadQuestion(qid) {
  currentQid = qid;
  try {
    const data = await apiGet("/api/questions/" + qid);
    $("question-title").textContent = topicLabel(data.topic) + " - " + data.qid;
    $("question-knowledge").textContent = "知识点：" + data.knowledge;
    $("question-text").textContent = data.question;
    $("answer-text").textContent = data.answer;
    $("answer-area").style.display = "none";
    $("show-answer-btn").style.display = "";
    $("submit-status").textContent = "";
    $("question-section").style.display = "";
    $("continue-section").style.display = "none";
    $("topic-section").style.display = "";
  } catch (e) {
    alert("加载题目失败：" + e.message);
  }
}

function showAnswer() {
  $("answer-area").style.display = "";
  $("show-answer-btn").style.display = "none";
}

async function submitScore(score) {
  const status = $("submit-status");
  setStatus(status, "保存中...", "");
  const btns = document.querySelectorAll(".score-btn");
  btns.forEach(b => b.disabled = true);
  try {
    const requestId = currentArchive + "-" + currentQid + "-" + Date.now();
    const data = await apiPost("/api/submit", {
      archive: currentArchive,
      request_id: requestId,
      qid: currentQid,
      score: score,
    });
    if (data.replayed) {
      setStatus(status, "已保存（重复提交，未新增记录）", "success");
    } else {
      const label = ["不会", "模糊", "掌握"][score];
      setStatus(status, "已保存！评分：" + label + " | 引用：" + JSON.stringify(data.citation).substring(0, 80) + "...", "success");
    }
  } catch (e) {
    setStatus(status, "保存失败：" + e.message + "（未记录为成功）", "error");
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

function backToTopics() {
  $("question-section").style.display = "none";
  $("continue-section").style.display = "none";
  $("topic-section").style.display = "";
}

async function showContinue() {
  $("question-section").style.display = "none";
  $("topic-section").style.display = "none";
  $("continue-section").style.display = "";
  try {
    const data = await apiGet("/api/progress?archive=" + encodeURIComponent(currentArchive));
    renderProgress(data);
  } catch (e) {
    $("progress-summary").innerHTML = '<p class="status error">加载进度失败：' + e.message + "</p>";
    $("weak-list").innerHTML = "";
    $("next-rec").innerHTML = "";
    $("recent-list").innerHTML = "";
  }
}

function renderProgress(data) {
  const summary = $("progress-summary");
  const total = Object.keys(data.scores).length;
  const unseen = data.unseen_qids.length;
  summary.innerHTML =
    statBox(data.mastered_count, "已掌握") +
    statBox(data.weak_qids.length, "薄弱") +
    statBox(data.fuzzy_qids.length, "模糊") +
    statBox(unseen, "未练习") +
    statBox(data.total_practices, "总练习次数");

  const weakList = $("weak-list");
  if (data.weak_qids.length) {
    weakList.innerHTML = "<h3>薄弱知识点</h3>" +
      data.weak_qids.map(qid =>
        '<div class="weak-item" onclick="loadQuestion(\'' + qid + '\')">' + qid + " - 不会</div>"
      ).join("");
  } else {
    weakList.innerHTML = "<p>暂无薄弱题，继续保持！</p>";
  }

  const rec = data.recommendation;
  const nextRec = $("next-rec");
  if (rec.qid) {
    nextRec.innerHTML =
      '<div class="rec-card">' +
      '<div class="reason">推荐下一题：' + rec.qid + "</div>" +
      "<div>" + rec.reason + "</div>" +
      '<button style="margin-top:8px" onclick="loadQuestion(\'' + rec.qid + '\')">开始复习</button>' +
      "</div>";
  } else {
    nextRec.innerHTML = "<p>所有题目已完成！</p>";
  }

  const recentList = $("recent-list");
  if (data.recent_practices.length) {
    recentList.innerHTML = "<h3>最近练习</h3>" +
      data.recent_practices.slice().reverse().map(p => {
        const label = ["不会", "模糊", "掌握"][p.score];
        return '<div class="recent-item" onclick="loadQuestion(\'' + p.qid + '\')">' +
          p.qid + " - " + label + " (第" + p.sequence + "次)" +
          "</div>";
      }).join("");
  } else {
    recentList.innerHTML = "<p>暂无练习记录</p>";
  }
}

function statBox(num, label) {
  return '<div class="stat-box"><div class="num">' + num + '</div><div class="label">' + label + "</div></div>";
}
