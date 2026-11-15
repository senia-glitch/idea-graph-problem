/* Диаграмма вариантов использования: роли слева, сценарии справа, линии — связи «участвует».
 * Строится из графа (S.graph): ничего отдельно не хранится.
 */

const clip = (value, limit) => { const t = String(value ?? "").replace(/\s+/g, " ").trim(); return t.length > limit ? t.slice(0, limit - 1) + "…" : t; };

export function build(graph) {
  const cards = new Map((graph?.cards || []).map((c) => [c.id, c]));
  const roles = (graph?.cards || []).filter((c) => c.kind === "роль");
  const scenarios = (graph?.cards || []).filter((c) => c.kind === "сценарий");
  const links = [];
  for (const edge of graph?.edges || []) {
    if (edge.kind === "участвует" && cards.get(edge.from)?.kind === "сценарий" && cards.get(edge.to)?.kind === "роль") links.push([edge.to, edge.from]);
  }
  return {roles, scenarios, links};
}

/* Рисует SVG-строку. */
export function svg(graph, {esc}) {
  const {roles, scenarios, links} = build(graph);
  if (!roles.length && !scenarios.length) return '<div class="hint">Роли и сценарии пока не созданы.</div>';
  const rowh = 54, top = 14, width = 640, actorX = 90, caseX = 440;
  const rows = Math.max(roles.length, scenarios.length, 1);
  const height = top * 2 + rowh * rows;
  const ay = (i) => top + rowh * i + rowh / 2;
  const roleIndex = new Map(roles.map((r, i) => [r.id, i]));
  const scenIndex = new Map(scenarios.map((s, i) => [s.id, i]));
  const out = [`<svg class="uc" viewBox="0 0 ${width} ${height}" width="${width}" height="${height}" xmlns="http://www.w3.org/2000/svg" role="img">`];
  for (const [roleId, scenarioId] of links) {
    out.push(`<line x1="${actorX + 22}" y1="${ay(roleIndex.get(roleId))}" x2="${caseX - 110}" y2="${ay(scenIndex.get(scenarioId))}" class="uc-link"/>`);
  }
  roles.forEach((role, i) => {
    const y = ay(i);
    out.push(`<g class="uc-actor"><title>${esc(role.title)}</title><circle cx="${actorX}" cy="${y - 10}" r="7"/><path d="M${actorX} ${y - 3} v14 M${actorX - 10} ${y + 2} h20 M${actorX} ${y + 11} l-8 11 M${actorX} ${y + 11} l8 11"/>`
      + `<text x="${actorX - 22}" y="${y + 6}" text-anchor="end">${esc(clip(role.title, 16))}</text></g>`);
  });
  scenarios.forEach((scenario, i) => {
    const y = ay(i), chosen = /(^|\n)\s*Автоматизировать\s*:\s*да\.?\s*$/im.test(scenario.body || "");
    out.push(`<g class="uc-case${chosen ? " chosen" : ""}"><title>${esc(scenario.title)}</title><ellipse cx="${caseX}" cy="${y}" rx="110" ry="22"/>`
      + `<text x="${caseX}" y="${y + 4}" text-anchor="middle">${esc(clip(scenario.title, 28))}</text></g>`);
  });
  out.push("</svg>");
  return out.join("");
}

/* Контракт оболочки: visual(box, {graph, esc}). */
export function visual(box, {graph, esc}) {
  box.innerHTML = `<div class="uc-wrap">${svg(graph, {esc})}</div>`;
}
