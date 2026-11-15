/* Диаграмма последовательности: разбор текста, отрисовка SVG и редактор шагов.
 *
 * Формат (attrs.diagram карточки): {type: "sequence", participants: [...], steps: [{from, to, text, pain?, mark?}]}.
 * Модуль не знает про оболочку: оболочка передаёт нужные функции (esc, save) аргументами.
 */

export const IMPORTANCE = ["низкая", "средняя", "высокая", "критическая"];
export const MARKS = {"": "обычный шаг", new: "новый", changed: "изменён", auto: "автоматизирован", removed: "убран"};
const MARK_SIGNS = {"+": "new", "~": "changed", "*": "auto", "-": "removed"};
const SIGN_OF = Object.fromEntries(Object.entries(MARK_SIGNS).map(([k, v]) => [v, k]));
const MARK_COLORS = {new: "#7fc97f", changed: "#e8c33a", auto: "#6fd3c7", removed: "#ff6b6b"};
const clip = (value, limit) => String(value ?? "").replace(/\s+/g, " ").trim().slice(0, limit);

export function normalize(raw) {
  const participants = [];
  for (const item of raw?.participants || []) {
    const name = clip(item, 60);
    if (name && !participants.includes(name)) participants.push(name);
  }
  const steps = [];
  for (const item of (raw?.steps || []).slice(0, 60)) {
    const from = clip(item?.from, 60), to = clip(item?.to, 60), text = clip(item?.text, 300);
    if (!from || !to || !text) continue;
    for (const name of [from, to]) if (!participants.includes(name)) participants.push(name);
    const step = {from, to, text, mark: Object.hasOwn(MARKS, item.mark || "") ? item.mark || "" : ""};
    const pain = item?.pain;
    if (pain && typeof pain === "object") {
      const clean = {what: clip(pain.what, 200), often: clip(pain.often, 80), cost: clip(pain.cost, 120), importance: clip(pain.importance, 20).toLowerCase()};
      if (!IMPORTANCE.includes(clean.importance)) clean.importance = "";
      if (Object.values(clean).some(Boolean)) step.pain = clean;
    }
    steps.push(step);
  }
  return {type: "sequence", participants, steps};
}

/* «1. + Кто → Кому: что [боль: что | как часто | цена | важность]» → диаграмма (то же, что parse_text в Python). */
export function parseText(text) {
  const steps = [];
  for (let line of String(text || "").split(/\r?\n/)) {
    line = line.replace(/^\s*\d+[.)]\s*/, "").trim();
    if (!line) continue;
    let mark = "";
    if (MARK_SIGNS[line[0]] && (line.length > 1 && /[ \t]/.test(line[1]) || !/[\p{L}\p{N}]/u.test(line[1] || ""))) {
      mark = MARK_SIGNS[line[0]];
      line = line.slice(1).trim();
    }
    let pain = null;
    const found = line.match(/\[\s*боль\s*:(.*?)\]\s*$/i);
    if (found) {
      line = line.slice(0, found.index).trim();
      const parts = found[1].split("|").map((p) => p.trim());
      pain = {what: parts[0] || "", often: parts[1] || "", cost: parts[2] || "", importance: parts[3] || ""};
    }
    const at = line.indexOf(":");
    if (at < 0) continue;
    const sides = line.slice(0, at).trim().split(/\s*(?:→|-->|->|=>)\s*/);
    const body = line.slice(at + 1).trim();
    if (sides.length < 2 || !body) continue;
    const step = {from: sides[0], to: sides.slice(1).join(" "), text: body, mark};
    if (pain) step.pain = pain;
    steps.push(step);
  }
  return normalize({participants: [], steps});
}

export function toText(diagram) {
  return normalize(diagram).steps.map((step, i) => {
    const sign = SIGN_OF[step.mark || ""] || "";
    let line = `${i + 1}. ${sign ? sign + " " : ""}${step.from} → ${step.to}: ${step.text}`;
    if (step.pain) line += ` [боль: ${step.pain.what} | ${step.pain.often} | ${step.pain.cost} | ${step.pain.importance}]`;
    return line;
  }).join("\n");
}

function wrap(text, width) {
  const words = String(text).split(" "), lines = [];
  let line = "";
  for (const word of words) {
    if ((line + " " + word).trim().length > width && line) { lines.push(line); line = word; }
    else line = (line + " " + word).trim();
  }
  if (line) lines.push(line);
  return lines.slice(0, 3);
}

/* Рисует диаграмму в SVG-строку. */
export function svg(raw, {esc}) {
  const d = normalize(raw);
  if (!d.participants.length) return '<div class="hint">Диаграмма пока пустая.</div>';
  const colw = 130, left = 20, top = 46, rowh = 52;
  const width = left * 2 + colw * d.participants.length, height = top + rowh * (d.steps.length + 1);
  const x = (name) => left + colw * d.participants.indexOf(name) + colw / 2;
  const parts = [`<svg class="seq" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg" role="img">`,
    '<defs><marker id="seqarrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0 0L10 5L0 10z" fill="currentColor"/></marker></defs>'];
  d.participants.forEach((name) => {
    const cx = x(name);
    parts.push(`<rect x="${cx - 56}" y="6" width="112" height="28" rx="6" class="seq-actor"/><text x="${cx}" y="25" text-anchor="middle" class="seq-name">${esc(clip(name, 16))}</text>`);
    parts.push(`<line x1="${cx}" y1="36" x2="${cx}" y2="${height - 8}" class="seq-life"/>`);
  });
  d.steps.forEach((step, i) => {
    const y = top + rowh * (i + 1) - 14, a = x(step.from), b = x(step.to);
    const color = MARK_COLORS[step.mark] || "currentColor";
    const dash = step.mark === "removed" ? ' stroke-dasharray="5 4"' : "";
    const lines = wrap(`${i + 1}. ${step.text}`, 24);
    if (a === b) {
      parts.push(`<path d="M${a} ${y - 6} h40 v14 h-40" fill="none" stroke="${color}" stroke-width="1.6"${dash} marker-end="url(#seqarrow)" style="color:${color}"/>`);
      lines.forEach((l, k) => parts.push(`<text x="${a + 46}" y="${y - 2 + k * 12}" class="seq-text" fill="${color}">${esc(l)}</text>`));
    } else {
      parts.push(`<line x1="${a}" y1="${y}" x2="${b}" y2="${y}" stroke="${color}" stroke-width="1.6"${dash} marker-end="url(#seqarrow)" style="color:${color}"/>`);
      const mid = (a + b) / 2;
      lines.forEach((l, k) => parts.push(`<text x="${mid}" y="${y - 6 - (lines.length - 1 - k) * 12}" text-anchor="middle" class="seq-text" fill="${color}"${step.mark === "removed" ? ' text-decoration="line-through"' : ""}>${esc(l)}</text>`));
    }
    if (step.pain) {
      const tip = [step.pain.what, step.pain.often, step.pain.cost, step.pain.importance && `важность: ${step.pain.importance}`].filter(Boolean).join(" · ");
      parts.push(`<g class="seq-pain"><title>${esc(tip)}</title><circle cx="${Math.max(a, b) + 14}" cy="${y}" r="8" fill="#ff6b6b"/><text x="${Math.max(a, b) + 14}" y="${y + 4}" text-anchor="middle" class="seq-pain-mark">!</text></g>`);
    }
  });
  parts.push("</svg>");
  return parts.join("");
}

/* Короткое тело карточки на холсте: число шагов, болей и отметок. */
export function node(card, {esc}) {
  const d = normalize(card.attrs?.diagram);
  if (!d.steps.length) return '<div class="seq-note">Диаграмма пока пустая: откройте карточку и добавьте шаги.</div>';
  const pains = d.steps.filter((s) => s.pain).length;
  const marks = d.steps.filter((s) => s.mark).length;
  return `<div class="seq-note">${d.participants.map((p) => esc(p)).join(" · ")}<br>шагов: ${d.steps.length}` +
    (pains ? ` · болей: <b class="seq-pain-count">${pains}</b>` : "") + (marks ? ` · отличий: ${marks}` : "") + "</div>";
}

/* Редактор в панели свойств. api: {esc, save(diagram), tobe}. */
export function editor(box, card, api) {
  const {esc, save, tobe} = api;
  let diagram = normalize(card.attrs?.diagram);
  const ownerOptions = (value) => diagram.participants.map((p) => `<option${p === value ? " selected" : ""}>${esc(p)}</option>`).join("");
  const render = () => {
    box.innerHTML = `
      <div class="lbl">Диаграмма последовательности</div>
      <div class="seq-view">${svg(diagram, {esc})}</div>
      <label>Участники (через запятую)<input data-p value="${esc(diagram.participants.join(", "))}"></label>
      <div class="seq-steps">${diagram.steps.map((s, i) => `
        <div class="seq-row" data-i="${i}">
          <b>${i + 1}</b>
          <select data-f="from">${ownerOptions(s.from)}</select><span>→</span><select data-f="to">${ownerOptions(s.to)}</select>
          <input data-f="text" value="${esc(s.text)}" placeholder="Что происходит">
          ${tobe ? `<select data-f="mark" title="Отличие от «как есть»">${Object.entries(MARKS).map(([k, n]) => `<option value="${k}"${(s.mark || "") === k ? " selected" : ""}>${n}</option>`).join("")}</select>` : ""}
          <button class="icon" data-up title="Выше">↑</button><button class="icon" data-down title="Ниже">↓</button><button class="icon danger" data-del title="Убрать шаг">✕</button>
          ${tobe ? "" : `<details class="seq-pain-edit"${s.pain ? " open" : ""}><summary>${s.pain ? "боль на шаге" : "добавить боль"}</summary>
            <input data-pain="what" value="${esc(s.pain?.what || "")}" placeholder="Что не так">
            <input data-pain="often" value="${esc(s.pain?.often || "")}" placeholder="Как часто">
            <input data-pain="cost" value="${esc(s.pain?.cost || "")}" placeholder="Чем это стоит">
            <select data-pain="importance"><option value="">важность</option>${IMPORTANCE.map((v) => `<option${s.pain?.importance === v ? " selected" : ""}>${v}</option>`).join("")}</select>
          </details>`}
        </div>`).join("")}</div>
      <div class="row-actions"><button data-add>+ Шаг</button></div>
      <label>Вставить текст шагов («Кто → Кому: что», по строке на шаг)<textarea data-text rows="4" placeholder="1. Пациент → Регистратура: звонит&#10;2. Регистратура → Пациент: предлагает время"></textarea></label>
      <div class="row-actions"><button data-parse>Построить из текста</button><button data-totext>Шаги в текст</button></div>`;
    bind();
  };
  const commit = () => { diagram = normalize(diagram); save(diagram); };
  const bind = () => {
    box.querySelector("[data-p]").onchange = (e) => {
      diagram.participants = e.target.value.split(",").map((p) => clip(p, 60)).filter(Boolean);
      for (const step of diagram.steps) for (const key of ["from", "to"]) if (!diagram.participants.includes(step[key])) diagram.participants.push(step[key]);
      commit(); render();
    };
    box.querySelectorAll(".seq-row").forEach((row) => {
      const i = Number(row.dataset.i);
      row.querySelectorAll("[data-f]").forEach((el) => { el.onchange = () => { diagram.steps[i][el.dataset.f] = el.value; commit(); render(); }; });
      row.querySelectorAll("[data-pain]").forEach((el) => {
        el.onchange = () => {
          const pain = {...(diagram.steps[i].pain || {})};
          pain[el.dataset.pain] = el.value;
          diagram.steps[i].pain = pain;
          commit(); render();
        };
      });
      row.querySelector("[data-del]").onclick = () => { diagram.steps.splice(i, 1); commit(); render(); };
      row.querySelector("[data-up]").onclick = () => { if (i > 0) { [diagram.steps[i - 1], diagram.steps[i]] = [diagram.steps[i], diagram.steps[i - 1]]; commit(); render(); } };
      row.querySelector("[data-down]").onclick = () => { if (i < diagram.steps.length - 1) { [diagram.steps[i + 1], diagram.steps[i]] = [diagram.steps[i], diagram.steps[i + 1]]; commit(); render(); } };
    });
    box.querySelector("[data-add]").onclick = () => {
      const [a, b] = [diagram.participants[0] || "Участник 1", diagram.participants[1] || diagram.participants[0] || "Участник 2"];
      if (!diagram.participants.length) diagram.participants.push(a, b);
      diagram.steps.push({from: a, to: b, text: "Новый шаг", mark: ""});
      commit(); render();
    };
    box.querySelector("[data-parse]").onclick = () => {
      const parsed = parseText(box.querySelector("[data-text]").value);
      if (!parsed.steps.length) return;
      diagram = parsed; commit(); render();
    };
    box.querySelector("[data-totext]").onclick = () => { box.querySelector("[data-text]").value = toText(diagram); };
  };
  render();
}
