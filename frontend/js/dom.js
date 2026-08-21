// Small DOM helpers. Everything user-visible goes through `el`/`text` so ESPN
// strings (team names, manager names) are inserted as text nodes and can never
// be interpreted as markup.

export function el(tag, attrs = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "dataset") Object.assign(node.dataset, value);
    else if (key.startsWith("on") && typeof value === "function") {
      node.addEventListener(key.slice(2).toLowerCase(), value);
    } else if (key === "html") node.innerHTML = value;
    else node.setAttribute(key, value === true ? "" : String(value));
  }
  for (const child of children.flat()) {
    if (child === null || child === undefined || child === false) continue;
    node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return node;
}

export function clear(node) {
  node.replaceChildren();
  return node;
}

export function card(title, meta, ...children) {
  return el(
    "section",
    { class: "card" },
    el(
      "div",
      { class: "card__head" },
      el("h2", { class: "card__title" }, title),
      meta ? el("span", { class: "card__meta" }, meta) : null,
    ),
    ...children,
  );
}

export function table(headers, rows) {
  const head = el(
    "tr",
    {},
    headers.map((header) =>
      el("th", { class: header.align === "right" ? "num" : null }, header.label ?? header),
    ),
  );
  return el(
    "div",
    { class: "table-wrap" },
    el("table", {}, el("thead", {}, head), el("tbody", {}, rows)),
  );
}

export function teamCell(team) {
  const owners = (team.owners || []).join(", ");
  return el(
    "div",
    { class: "team" },
    team.logo ? el("img", { class: "team__logo", src: team.logo, alt: "", loading: "lazy" }) : null,
    el(
      "div",
      {},
      el("span", { class: "team__name" }, team.name || `Team ${team.team_id}`),
      owners ? el("span", { class: "team__owner" }, owners) : null,
    ),
  );
}

export function empty(message) {
  return el("div", { class: "empty" }, message);
}

export function points(value) {
  return Number(value ?? 0).toFixed(1);
}

export function formatDate(ms) {
  if (!ms) return "-";
  return new Date(Number(ms)).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}
