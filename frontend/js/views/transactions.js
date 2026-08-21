import { card, el, empty, formatDate, table } from "../dom.js";

export function renderTransactions(data) {
  const rows = data.transactions || [];
  if (!rows.length) {
    return card(
      "Transactions",
      null,
      empty("No adds, drops, or trades recorded for this league yet."),
    );
  }

  const body = rows.map((txn) =>
    el(
      "tr",
      {},
      el("td", {}, formatDate(txn.processed_date_ms)),
      el("td", {}, el("span", { class: "pill pill--accent" }, txn.label)),
      el("td", { class: "name" }, txn.team_name || el("span", { class: "muted" }, "-")),
      el("td", { class: "name" }, itemList(txn.items)),
      el(
        "td",
        { class: "num" },
        txn.bid_amount ? `$${txn.bid_amount}` : el("span", { class: "muted" }, "-"),
      ),
    ),
  );

  return card(
    "Transactions",
    `${rows.length} most recent`,
    table(
      ["Date", "Type", "Team", "Players", { label: "Bid", align: "right" }],
      body,
    ),
  );
}

function itemList(items) {
  return el(
    "div",
    {},
    (items || []).map((item) =>
      el(
        "div",
        {},
        el("span", { class: "team__name" }, item.player_name),
        " ",
        el("span", { class: "muted" }, describe(item)),
      ),
    ),
  );
}

function describe(item) {
  if (item.action === "TRADE" && item.from_team_name && item.to_team_name) {
    return `traded ${item.from_team_name} → ${item.to_team_name}`;
  }
  if (item.action === "ADD" && item.to_team_name) return `added by ${item.to_team_name}`;
  if (item.action === "DROP" && item.from_team_name) return `dropped by ${item.from_team_name}`;
  return item.action_label;
}
