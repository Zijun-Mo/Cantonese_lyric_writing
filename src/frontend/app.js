/* global fetch */

const $ = (id) => document.getElementById(id);

let _progressTimer = null;
let _runAbort = null;

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

function joinBars(arr) {
  return (arr || []).map((s) => String(s || "").trim()).join(" | ");
}

function buildBarsFromEditor(rows) {
  const jBars = [];
  const mBars = [];
  for (const r of rows || []) {
    jBars.push(String(r.jianpu || "").trim());
    mBars.push(String(r.mandarin || "").trim());
  }
  return {
    jianpu: joinBars(jBars),
    mandarin_seed: joinBars(mBars),
  };
}

function createSheetEditor(containerEl) {
  const rows = [];

  function addRow(initial = {}) {
    rows.push({
      jianpu: String(initial.jianpu || ""),
      mandarin: String(initial.mandarin || ""),
    });
  }

  function clearRows() {
    rows.splice(0, rows.length);
  }

  function setFromBars(jBars, mBars) {
    clearRows();
    const max = Math.max((jBars || []).length, (mBars || []).length);
    for (let i = 0; i < max; i++) addRow({ jianpu: jBars?.[i] || "", mandarin: mBars?.[i] || "" });
    if (max === 0) addRow({});
    render();
  }

  function render() {
    containerEl.innerHTML = "";
    if (!rows.length) addRow({});

    const header = document.createElement("div");
    header.className = "sheetInputHead";
    header.innerHTML = `
      <div class="colIdx">小节</div>
      <div class="colJ">简谱（空格分隔）</div>
      <div class="colM">普通话</div>
      <div class="colOp"></div>
    `;
    containerEl.appendChild(header);

    for (let i = 0; i < rows.length; i++) {
      const r = rows[i];
      const line = document.createElement("div");
      line.className = "sheetInputRow";

      const idx = document.createElement("div");
      idx.className = "colIdx";
      idx.textContent = String(i + 1);

      const j = document.createElement("input");
      j.className = "colJ";
      j.type = "text";
      j.value = r.jianpu;
      j.placeholder = "例如：0 3 6 5 6 3 2";
      j.addEventListener("input", () => {
        r.jianpu = j.value;
      });

      const m = document.createElement("input");
      m.className = "colM";
      m.type = "text";
      m.value = r.mandarin;
      m.placeholder = "例如：我是在时间的树下等了你很久";
      m.addEventListener("input", () => {
        r.mandarin = m.value;
      });

      const op = document.createElement("div");
      op.className = "colOp";
      const del = document.createElement("button");
      del.className = "btn btn-ghost btn-mini";
      del.type = "button";
      del.textContent = "×";
      del.title = "删除该小节";
      del.addEventListener("click", () => {
        rows.splice(i, 1);
        if (!rows.length) addRow({});
        render();
      });
      op.appendChild(del);

      line.appendChild(idx);
      line.appendChild(j);
      line.appendChild(m);
      line.appendChild(op);
      containerEl.appendChild(line);
    }
  }

  addRow({});
  render();

  return {
    getRows: () => rows.map((r) => ({ ...r })),
    addRow: () => {
      addRow({});
      render();
    },
    clear: () => {
      clearRows();
      addRow({});
      render();
    },
    setFromBars,
  };
}

async function fetchDemo() {
  const resp = await fetch("/api/demo");
  const data = await resp.json();
  if (!resp.ok || !data?.ok) throw new Error(data?.error || `HTTP ${resp.status}`);
  return data.demo;
}

function onlyHanChars(s) {
  return Array.from(String(s || "")).filter((ch) => ch >= "\u4e00" && ch <= "\u9fff");
}

function tokenizeJianpuBar(bar) {
  return String(bar || "")
    .trim()
    .split(/\s+/g)
    .map((t) => t.trim())
    .filter(Boolean);
}

function pickNoteTokens(tokens, slotCount) {
  if (!slotCount || slotCount <= 0) return [];
  const prefer = tokens.filter((t) => t !== "-" && t !== "0");
  const alt = tokens.filter((t) => t !== "-");
  const chosen = (prefer.length >= slotCount ? prefer : alt).slice(0, slotCount);
  while (chosen.length < slotCount) chosen.push("");
  return chosen;
}

function padOrTruncChars(chars, n) {
  const out = chars.slice(0, n);
  while (out.length < n) out.push("");
  return out;
}

function parseJianpuToken(raw) {
  let t = String(raw || "").trim();
  let up = 0;
  let down = 0;
  while (t.startsWith("^")) {
    up += 1;
    t = t.slice(1);
  }
  while (t.startsWith("v")) {
    down += 1;
    t = t.slice(1);
  }
  return { base: t, up, down };
}

function addOctaveDots(svg, x, y, count, direction, color) {
  // direction: -1 for up, +1 for down
  const ns = "http://www.w3.org/2000/svg";
  for (let i = 0; i < count; i++) {
    const cy = y + direction * (10 + i * 6);
    const dot = document.createElementNS(ns, "circle");
    dot.setAttribute("cx", String(x));
    dot.setAttribute("cy", String(cy));
    dot.setAttribute("r", "1.7");
    dot.setAttribute("fill", color);
    svg.appendChild(dot);
  }
}

function renderResult({ jianpuBars, mandarinBars, cantoBars, barScores, barMeta }) {
  const box = $("scoreBox");
  box.innerHTML = "";
  const maxBars = Math.max(jianpuBars.length, mandarinBars.length, cantoBars.length, barMeta?.length || 0);

  const sheet = document.createElement("div");
  sheet.className = "sheet";

  for (let i = 0; i < maxBars; i++) {
    const meta = barMeta?.[i] || {};
    const isRest = Boolean(meta.is_rest);
    const slotCount = Number(meta.slot_count ?? 0) || 0;
    const score = barScores?.[i];

    const measure = document.createElement("div");
    measure.className = `measure${isRest ? " rest" : ""}`;

    const head = document.createElement("div");
    head.className = "measureHead";
    head.innerHTML = `第 ${i + 1} 小节${score != null ? `<span class="measureScore">score ${Number(score).toFixed(3)}</span>` : ""}`;
    measure.appendChild(head);

    if (isRest || slotCount <= 0) {
      const rest = document.createElement("div");
      rest.className = "measureRest";
      rest.textContent = "（休止/空小节）";
      measure.appendChild(rest);
      sheet.appendChild(measure);
      continue;
    }

    const staff = document.createElement("div");
    staff.className = "staff";

    const grid = document.createElement("div");
    grid.className = "slotGrid";
    grid.style.gridTemplateColumns = `repeat(${slotCount}, minmax(20px, 1fr))`;

    const jTokens = pickNoteTokens(tokenizeJianpuBar(jianpuBars[i]), slotCount);
    const mandChars = padOrTruncChars(onlyHanChars(mandarinBars[i]), slotCount);
    const cantoChars = padOrTruncChars(onlyHanChars(cantoBars[i]), slotCount);

    // Row 1: notes
    for (let s = 0; s < slotCount; s++) {
      const cell = document.createElement("div");
      cell.className = "slotCell noteCell";
      cell.textContent = jTokens[s] || "";
      grid.appendChild(cell);
    }
    // Row 2: mandarin
    for (let s = 0; s < slotCount; s++) {
      const cell = document.createElement("div");
      cell.className = "slotCell lyricCell mand";
      cell.textContent = mandChars[s] || "";
      grid.appendChild(cell);
    }
    // Row 3: canto
    for (let s = 0; s < slotCount; s++) {
      const cell = document.createElement("div");
      cell.className = "slotCell lyricCell canto";
      cell.textContent = cantoChars[s] || "";
      grid.appendChild(cell);
    }

    staff.appendChild(grid);
    measure.appendChild(staff);
    sheet.appendChild(measure);
  }

  box.appendChild(sheet);
}

function renderFullScoreSVG({ jianpuBars, mandarinBars, cantoBars, barMeta, barScores, mode }) {
  const host = $("scoreBoxFull");
  if (!host) return;
  host.innerHTML = "";

  const maxBars = Math.max(jianpuBars.length, mandarinBars.length, cantoBars.length, barMeta?.length || 0);
  const slotW = 28;
  const padX = 18;
  const measureGap = 10;
  const staffTop = 26;
  const staffH = 56;
  const lineGap = 7;
  const lyricY1 = staffTop + staffH + 18;
  const lyricY2 = lyricY1 + 18;
  const headY = 14;
  const lineH = lyricY2 + 26;

  const measures = [];
  for (let i = 0; i < maxBars; i++) {
    const meta = barMeta?.[i] || {};
    const isRest = Boolean(meta.is_rest);
    const slotCount = Number(meta.slot_count ?? 0) || 0;
    const w = isRest || slotCount <= 0 ? 90 : Math.max(90, slotCount * slotW + 16);
    measures.push({ i, isRest, slotCount, w });
  }

  const maxLineW = Math.max(520, (host.clientWidth || 980) - 28);
  const lines = [];
  let cur = [];
  let curW = padX;
  for (const m of measures) {
    if (cur.length && curW + m.w + measureGap + padX > maxLineW) {
      lines.push(cur);
      cur = [];
      curW = padX;
    }
    cur.push(m);
    curW += m.w + measureGap;
  }
  if (cur.length) lines.push(cur);

  const totalW = maxLineW;
  const totalH = lines.length * lineH + padX;

  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("class", "fullScoreSvg");
  svg.setAttribute("viewBox", `0 0 ${totalW} ${totalH}`);
  svg.setAttribute("preserveAspectRatio", "xMinYMin meet");

  const bg = document.createElementNS(ns, "rect");
  bg.setAttribute("x", "0");
  bg.setAttribute("y", "0");
  bg.setAttribute("width", String(totalW));
  bg.setAttribute("height", String(totalH));
  bg.setAttribute("rx", "18");
  bg.setAttribute("fill", "rgba(255,255,255,0.03)");
  bg.setAttribute("stroke", "rgba(255,255,255,0.14)");
  svg.appendChild(bg);

  const baseFont =
    "ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, 'Microsoft YaHei', 'PingFang SC', 'Noto Sans CJK SC', 'Noto Sans SC'";
  const monoFont =
    "ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace, 'Microsoft YaHei'";

  for (let li = 0; li < lines.length; li++) {
    const yOff = li * lineH;
    let x = padX;
    const lineMs = lines[li];

    // left boundary for each line
    const leftBoundary = document.createElementNS(ns, "line");
    leftBoundary.setAttribute("x1", String(x));
    leftBoundary.setAttribute("x2", String(x));
    leftBoundary.setAttribute("y1", String(yOff + staffTop - 6));
    leftBoundary.setAttribute("y2", String(yOff + lyricY2 + 12));
    leftBoundary.setAttribute("stroke", "rgba(255,255,255,0.14)");
    leftBoundary.setAttribute("stroke-width", "1");
    svg.appendChild(leftBoundary);

    for (const m of lineMs) {
      const { i, isRest, slotCount, w } = m;

      // staff lines
      for (let k = 0; k < 5; k++) {
        const y = yOff + staffTop + k * lineGap;
        const ln = document.createElementNS(ns, "line");
        ln.setAttribute("x1", String(x + 6));
        ln.setAttribute("x2", String(x + w - 6));
        ln.setAttribute("y1", String(y));
        ln.setAttribute("y2", String(y));
        ln.setAttribute("stroke", "rgba(255,255,255,0.18)");
        ln.setAttribute("stroke-width", "1");
        svg.appendChild(ln);
      }

      // bar borders
      const right = document.createElementNS(ns, "line");
      right.setAttribute("x1", String(x + w));
      right.setAttribute("x2", String(x + w));
      right.setAttribute("y1", String(yOff + staffTop - 6));
      right.setAttribute("y2", String(yOff + lyricY2 + 12));
      right.setAttribute("stroke", "rgba(255,255,255,0.22)");
      right.setAttribute("stroke-width", "1");
      svg.appendChild(right);

      const title = document.createElementNS(ns, "text");
      title.setAttribute("x", String(x + 6));
      title.setAttribute("y", String(yOff + headY));
      title.setAttribute("fill", "rgba(255,255,255,0.65)");
      title.setAttribute("font-family", monoFont);
      title.setAttribute("font-size", "11");
      const sc = barScores?.[i];
      title.textContent =
        mode === "preview"
          ? `bar ${i + 1}`
          : `bar ${i + 1}${sc != null ? `  score ${Number(sc).toFixed(3)}` : ""}`;
      svg.appendChild(title);

      if (isRest || slotCount <= 0) {
        const rest = document.createElementNS(ns, "text");
        rest.setAttribute("x", String(x + w / 2));
        rest.setAttribute("y", String(yOff + staffTop + 22));
        rest.setAttribute("text-anchor", "middle");
        rest.setAttribute("fill", "rgba(255,255,255,0.55)");
        rest.setAttribute("font-family", monoFont);
        rest.setAttribute("font-size", "12");
        rest.textContent = "REST";
        svg.appendChild(rest);
        x += w + measureGap;
        continue;
      }

      const jTokens = pickNoteTokens(tokenizeJianpuBar(jianpuBars[i]), slotCount);
      const mandChars = padOrTruncChars(onlyHanChars(mandarinBars[i]), slotCount);
      const cantoChars = padOrTruncChars(onlyHanChars(cantoBars[i]), slotCount);

      const innerX = x + 10;
      for (let s = 0; s < slotCount; s++) {
        const cx = innerX + s * slotW + slotW / 2;

        const parsed = parseJianpuToken(jTokens[s] || "");
        if (parsed.up > 0) {
          addOctaveDots(svg, cx, yOff + staffTop + 15, parsed.up, -1, "rgba(255,255,255,0.86)");
        }
        if (parsed.down > 0) {
          addOctaveDots(svg, cx, yOff + staffTop + 15, parsed.down, +1, "rgba(255,255,255,0.78)");
        }

        const note = document.createElementNS(ns, "text");
        note.setAttribute("x", String(cx));
        note.setAttribute("y", String(yOff + staffTop + 18));
        note.setAttribute("text-anchor", "middle");
        note.setAttribute("fill", "rgba(255,255,255,0.92)");
        note.setAttribute("font-family", monoFont);
        note.setAttribute("font-size", "15");
        note.setAttribute("font-weight", "700");
        note.textContent = parsed.base || "";
        svg.appendChild(note);

        const m1 = document.createElementNS(ns, "text");
        m1.setAttribute("x", String(cx));
        m1.setAttribute("y", String(yOff + lyricY1));
        m1.setAttribute("text-anchor", "middle");
        m1.setAttribute("fill", "rgba(255,255,255,0.78)");
        m1.setAttribute("font-family", baseFont);
        m1.setAttribute("font-size", "13");
        m1.textContent = mandChars[s] || "";
        svg.appendChild(m1);

        const m2 = document.createElementNS(ns, "text");
        m2.setAttribute("x", String(cx));
        m2.setAttribute("y", String(yOff + lyricY2));
        m2.setAttribute("text-anchor", "middle");
        m2.setAttribute("fill", "rgba(255,255,255,0.96)");
        m2.setAttribute("font-family", baseFont);
        m2.setAttribute("font-size", "13");
        m2.textContent = cantoChars[s] || "";
        svg.appendChild(m2);
      }

      x += w + measureGap;
    }
  }

  host.appendChild(svg);
}

function showProgress(show, stepText) {
  const panel = $("progressPanel");
  if (!panel) return;
  panel.style.display = show ? "" : "none";
  const step = $("progressStep");
  if (step && stepText != null) step.textContent = stepText;
}

async function pollProgress() {
  try {
    const r = await fetch("/api/progress");
    const data = await r.json();
    if (!data?.ok) return;
    const lines = Array.isArray(data.logs) ? data.logs : [];
    const tail = lines.slice(Math.max(0, lines.length - 30));
    const header = data.step || (data.running ? "运行中…" : "就绪");
    const text = `${header}\n\n${tail.join("\n")}`.trim();
    showProgress(true, text || "准备中…");
  } catch {
    showProgress(true, "（无法获取进度：请确认服务仍在运行）");
  }
}

function startProgressPolling() {
  stopProgressPolling();
  showProgress(true, "准备中…");
  pollProgress();
  _progressTimer = setInterval(() => pollProgress(), 600);
}

function stopProgressPolling() {
  if (_progressTimer) clearInterval(_progressTimer);
  _progressTimer = null;
}

async function run(payloadOverride) {
  setHint("hintKey", "", null);
  setHint("hintInput", "", null);

  const apiKey = $("apiKey").value.trim();
  const hasLocalKey = window.__hasLocalKey === true;
  if (!apiKey && !hasLocalKey) {
    setHint("hintKey", "请先输入 API Key（或在项目根目录放置非空 APIKey.txt）", "error");
    setStatus("缺少 API Key", "bad");
    return;
  }

  const base = buildBarsFromEditor(sheetEditor.getRows());
  const payload = {
    ...base,
    theme_tags: themeTagInput.getTags(),
    style_tags: styleTagInput.getTags(),
    candidates: Number($("candidates").value || 10),
    no_polish: Boolean($("noPolish").checked),
    ...(payloadOverride || {}),
  };

  if (!payload.jianpu.trim() || !payload.mandarin_seed.trim()) {
    setHint("hintInput", "请至少填写一行小节：简谱 + 普通话", "error");
    setStatus("输入不完整", "bad");
    return;
  }

  const jBars = splitBars(payload.jianpu);
  const mBars = splitBars(payload.mandarin_seed);
  if (jBars.length !== mBars.length) {
    setHint("hintInput", `小节数不一致：简谱 ${jBars.length}，普通话 ${mBars.length}（请检查行数/| 数量）`, "error");
    setStatus("小节数不一致", "bad");
    return;
  }

  $("btnRun").disabled = true;
  const btnRunDemo = document.getElementById("btnRunDemo");
  if (btnRunDemo) btnRunDemo.disabled = true;
  const btnCancel = document.getElementById("btnCancel");
  if (btnCancel) btnCancel.disabled = false;
  startProgressPolling();
  setStatus("运行中…", "busy");

  try {
    _runAbort = new AbortController();
    const headers = {
      "Content-Type": "application/json",
    };
    if (apiKey) headers.Authorization = `Bearer ${apiKey}`;
    const resp = await fetch("/api/run", {
      method: "POST",
      signal: _runAbort.signal,
      headers: {
        ...headers,
      },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (!resp.ok || !data?.ok) {
      const msg = data?.error || `HTTP ${resp.status}`;
      if (msg === "cancelled" || resp.status === 499) {
        setStatus("已终止", "bad");
        setHint("hintInput", "已发送终止请求（后台会尽快停止）", "error");
      } else {
        setStatus("运行失败", "bad");
        setHint("hintInput", msg, "error");
      }
      return;
    }

    const result = data.result;
    const cantoBars = splitBars(result?.full_lyric || "");
    const barScores = Array.isArray(result?.bars)
      ? result.bars.map((b) => (b?.score?.total != null ? Number(b.score.total) : null))
      : [];
    const barMeta = Array.isArray(result?.bars)
      ? result.bars.map((b) => ({ is_rest: Boolean(b?.is_rest), slot_count: Number(b?.slot_count ?? 0) }))
      : [];

    renderResult({
      jianpuBars: jBars,
      mandarinBars: mBars,
      cantoBars,
      barScores,
      barMeta,
    });
    renderFullScoreSVG({
      jianpuBars: jBars,
      mandarinBars: mBars,
      cantoBars,
      barMeta,
      barScores,
      mode: "final",
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
    showProgress(false);
  } catch (e) {
    if (e?.name === "AbortError") {
      setStatus("已终止", "bad");
      setHint("hintInput", "已终止请求（后台仍会尽快停止）", "error");
    } else {
      setStatus("运行异常", "bad");
      setHint("hintInput", String(e?.message || e), "error");
    }
  } finally {
    stopProgressPolling();
    $("btnRun").disabled = false;
    if (btnRunDemo) btnRunDemo.disabled = false;
    if (btnCancel) btnCancel.disabled = true;
    _runAbort = null;
  }
}

const themeTagInput = createTagInput($("themeTags"));
const styleTagInput = createTagInput($("styleTags"));
const sheetEditor = createSheetEditor($("sheetInput"));

$("btnRun").addEventListener("click", () => run());
$("btnAddBar").addEventListener("click", () => sheetEditor.addRow());
$("btnClearBars").addEventListener("click", () => sheetEditor.clear());
$("btnLoadDemo").addEventListener("click", async () => {
  try {
    setStatus("加载 Demo…", "busy");
    const demo = await fetchDemo();
    sheetEditor.setFromBars(splitBars(demo.jianpu), splitBars(demo.mandarin_seed));
    themeTagInput.setTags(demo.theme_tags || []);
    styleTagInput.setTags(demo.style_tags || []);
    setStatus("就绪", null);
    setHint("hintInput", "已填充 Demo，可以直接运行生成", "ok");
  } catch (e) {
    setStatus("加载失败", "bad");
    setHint("hintInput", String(e?.message || e), "error");
  }
});

$("btnRunDemo").addEventListener("click", async () => {
  try {
    const apiKey = $("apiKey").value.trim();
    const hasLocalKey = window.__hasLocalKey === true;
    if (!apiKey && !hasLocalKey) {
      setHint("hintKey", "请先输入 API Key（或在项目根目录放置非空 APIKey.txt）", "error");
      setStatus("缺少 API Key", "bad");
      return;
    }
    setStatus("运行 Demo…", "busy");
    const demo = await fetchDemo();
    themeTagInput.setTags(demo.theme_tags || []);
    styleTagInput.setTags(demo.style_tags || []);
    await run({
      jianpu: demo.jianpu,
      mandarin_seed: demo.mandarin_seed,
      theme_tags: demo.theme_tags || [],
      style_tags: demo.style_tags || [],
      candidates: 5,
      no_polish: true,
    });
  } catch (e) {
    setStatus("运行失败", "bad");
    setHint("hintInput", String(e?.message || e), "error");
  }
});

const btnCancel = document.getElementById("btnCancel");
if (btnCancel) {
  btnCancel.addEventListener("click", async () => {
    try {
      btnCancel.disabled = true;
      setStatus("终止中…", "busy");
      showProgress(true, "已请求终止…");
      try {
        await fetch("/api/cancel", { method: "POST" });
      } catch {
        // ignore
      }
      if (_runAbort) _runAbort.abort();
    } finally {
      // allow UI to recover in run() finally
    }
  });
}

const fileJson = document.getElementById("fileJson");
if (fileJson) {
  fileJson.addEventListener("change", async () => {
    const f = fileJson.files?.[0];
    if (!f) return;
    try {
      setStatus("读取 JSON…", "busy");
      const text = await f.text();
      const data = JSON.parse(text);
      const j = String(data?.jianpu || "").trim();
      const m = String(data?.mandarin_seed || "").trim();
      sheetEditor.setFromBars(splitBars(j), splitBars(m));
      themeTagInput.setTags(data?.theme_tags || []);
      styleTagInput.setTags(data?.style_tags || []);
      setStatus("就绪", null);
      setHint("hintInput", `已从文件载入：${f.name}`, "ok");
    } catch (e) {
      setStatus("读取失败", "bad");
      setHint("hintInput", String(e?.message || e), "error");
    } finally {
      fileJson.value = "";
    }
  });
}

$("btnRenderSvg").addEventListener("click", () => {
  try {
    const base = buildBarsFromEditor(sheetEditor.getRows());
    if (!base.jianpu.trim() || !base.mandarin_seed.trim()) {
      setHint("hintInput", "请先填写至少一行小节，再生成 SVG 预览", "error");
      return;
    }
    const jBars = splitBars(base.jianpu);
    const mBars = splitBars(base.mandarin_seed);
    const barMeta = jBars.map((jb, idx) => {
      const tokens = tokenizeJianpuBar(jb);
      const slotCount = Math.max(
        pickNoteTokens(tokens, 999).filter((x) => x !== "").length,
        onlyHanChars(mBars[idx] || "").length,
        1
      );
      const isRest = slotCount <= 0 || tokens.every((t) => t === "0" || t === "-" || t === "");
      return { is_rest: isRest, slot_count: isRest ? 0 : slotCount };
    });
    renderFullScoreSVG({
      jianpuBars: jBars,
      mandarinBars: mBars,
      cantoBars: mBars, // 预览阶段先用普通话占位，便于看对齐
      barMeta,
      barScores: [],
      mode: "preview",
    });
    setHint("hintInput", "已生成 SVG 预览（不调用后端）", "ok");
  } catch (e) {
    setHint("hintInput", String(e?.message || e), "error");
  }
});

$("btnClearKey").addEventListener("click", () => {
  $("apiKey").value = "";
  setHint("hintKey", "已清空（内存中不再保留）", "ok");
});

setStatus("就绪", null);

// detect local APIKey.txt availability (server-side only)
window.__hasLocalKey = false;
fetch("/api/key_status")
  .then((r) => r.json())
  .then((d) => {
    if (d?.ok && d.has_key) {
      window.__hasLocalKey = true;
      const input = document.getElementById("apiKey");
      if (input && !input.value) {
        input.placeholder = "已检测到本地 APIKey.txt，可留空直接运行";
      }
      setHint("hintKey", "已检测到本地 APIKey.txt，可不输入 API Key 直接运行", "ok");
    }
  })
  .catch(() => {});

