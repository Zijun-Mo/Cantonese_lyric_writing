/* global fetch */

const $ = (id) => document.getElementById(id);

function setStatus(text, cls) {
  const el = $("status");
  el.textContent = text;
  el.classList.remove("ok", "bad", "busy");
  if (cls) el.classList.add(cls);
}

function setHint(id, text, kind) {
  const el = $(id);
  el.textContent = text || "";
  el.classList.remove("error", "ok");
  if (kind) el.classList.add(kind);
}

function splitBars(text) {
  return String(text || "")
    .split("|")
    .map((s) => s.trim());
}

function normalizeTag(tag) {
  const t = String(tag || "").trim();
  if (!t) return "";
  return t.replace(/\s+/g, " ");
}

function createTagInput(containerEl) {
  const tags = [];
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "输入后回车 / 逗号";
  input.autocomplete = "off";
  input.spellcheck = false;
  containerEl.appendChild(input);

  function render() {
    // keep input as last child
    Array.from(containerEl.querySelectorAll(".tag")).forEach((n) => n.remove());
    tags.forEach((t, idx) => {
      const chip = document.createElement("span");
      chip.className = "tag";
      chip.innerHTML = `<span>${escapeHtml(t)}</span>`;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.title = "移除";
      btn.textContent = "×";
      btn.addEventListener("click", () => {
        tags.splice(idx, 1);
        render();
      });
      chip.appendChild(btn);
      containerEl.insertBefore(chip, input);
    });
  }

  function addFromRaw(raw) {
    const parts = String(raw || "")
      .split(/[,\uFF0C]/g)
      .map((s) => normalizeTag(s))
      .filter(Boolean);
    for (const p of parts) {
      if (!tags.includes(p)) tags.push(p);
    }
    if (parts.length) render();
  }

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      addFromRaw(input.value);
      input.value = "";
    } else if (e.key === "Backspace" && !input.value && tags.length) {
      tags.pop();
      render();
    }
  });
  input.addEventListener("blur", () => {
    addFromRaw(input.value);
    input.value = "";
  });

  render();
  return {
    getTags: () => tags.slice(),
    clear: () => {
      tags.splice(0, tags.length);
      input.value = "";
      render();
    },
    setTags: (arr) => {
      tags.splice(0, tags.length);
      for (const t of arr || []) {
        const nt = normalizeTag(t);
        if (nt && !tags.includes(nt)) tags.push(nt);
      }
      render();
    },
  };
}

function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function renderResult({ jianpuBars, mandarinBars, cantoBars, barScores }) {
  const box = $("scoreBox");
  box.innerHTML = "";
  const maxBars = Math.max(jianpuBars.length, mandarinBars.length, cantoBars.length);

  for (let i = 0; i < maxBars; i++) {
    const row = document.createElement("div");
    row.className = "barRow";

    const idx = document.createElement("div");
    idx.className = "barIdx";
    const score = barScores?.[i];
    idx.innerHTML = `bar ${i + 1}${score != null ? `<div class="muted">score ${score.toFixed(3)}</div>` : ""}`;
    row.appendChild(idx);

    const lines = document.createElement("div");
    lines.className = "lines";

    const addLine = (label, content, extraCls) => {
      const line = document.createElement("div");
      line.className = "line";
      const l = document.createElement("div");
      l.className = "label";
      l.textContent = label;
      const c = document.createElement("div");
      c.className = `content ${extraCls || ""}`.trim();
      c.textContent = content || "";
      line.appendChild(l);
      line.appendChild(c);
      lines.appendChild(line);
    };

    addLine("JIANPU", jianpuBars[i] || "");
    addLine("MAND", mandarinBars[i] || "");
    addLine("CANTO", cantoBars[i] || "", "canto");

    row.appendChild(lines);
    box.appendChild(row);
  }
}

async function run() {
  setHint("hintKey", "", null);
  setHint("hintInput", "", null);

  const apiKey = $("apiKey").value.trim();
  if (!apiKey) {
    setHint("hintKey", "请先输入 API Key（仅保存在内存中）", "error");
    setStatus("缺少 API Key", "bad");
    return;
  }

  const jianpu = $("jianpu").value.trim();
  const mandarin_seed = $("mandarin").value.trim();
  if (!jianpu || !mandarin_seed) {
    setHint("hintInput", "请填写简谱与普通话歌词（都需要用 | 分小节）", "error");
    setStatus("输入不完整", "bad");
    return;
  }

  const jBars = splitBars(jianpu);
  const mBars = splitBars(mandarin_seed);
  if (jBars.length !== mBars.length) {
    setHint("hintInput", `小节数不一致：简谱 ${jBars.length}，普通话 ${mBars.length}（请检查 | 数量）`, "error");
    setStatus("小节数不一致", "bad");
    return;
  }

  const payload = {
    jianpu,
    mandarin_seed,
    theme_tags: themeTagInput.getTags(),
    style_tags: styleTagInput.getTags(),
    candidates: Number($("candidates").value || 10),
    no_polish: Boolean($("noPolish").checked),
  };

  $("btnRun").disabled = true;
  setStatus("运行中…", "busy");

  try {
    const resp = await fetch("/api/run", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${apiKey}`,
      },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data?.ok) {
      const msg = data?.error || `HTTP ${resp.status}`;
      setStatus("运行失败", "bad");
      setHint("hintInput", msg, "error");
      return;
    }

    const result = data.result;
    const cantoBars = splitBars(result?.full_lyric || "");
    const barScores = Array.isArray(result?.bars)
      ? result.bars.map((b) => (b?.score?.total != null ? Number(b.score.total) : null))
      : [];

    renderResult({
      jianpuBars: jBars,
      mandarinBars: mBars,
      cantoBars,
      barScores,
    });

    const avg = result?.stats?.avg_total_score;
    const avgTone = result?.stats?.avg_tone_score;
    $("statScore").textContent =
      avg != null
        ? `评分：总分 ${Number(avg).toFixed(4)} / 协音 ${Number(avgTone ?? 0).toFixed(4)}`
        : "评分：-";
    $("statBars").textContent =
      result?.stats?.total_bars != null
        ? `小节：${result.stats.total_bars}，可唱：${result.stats.singable_bars ?? "-"}`
        : "";

    setStatus("完成", "ok");
    setHint("hintInput", "已生成并对齐展示（简谱 / 普通话 / 粤语）", "ok");
  } catch (e) {
    setStatus("运行异常", "bad");
    setHint("hintInput", String(e?.message || e), "error");
  } finally {
    $("btnRun").disabled = false;
  }
}

const themeTagInput = createTagInput($("themeTags"));
const styleTagInput = createTagInput($("styleTags"));

$("btnRun").addEventListener("click", () => run());
$("btnClearKey").addEventListener("click", () => {
  $("apiKey").value = "";
  setHint("hintKey", "已清空（内存中不再保留）", "ok");
});

// demo: keep empty by default, but provide quick paste helper if user wants
setStatus("就绪", null);

