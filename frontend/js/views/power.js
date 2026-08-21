import { card, el, empty, points, table, teamCell } from "../dom.js";

export function renderPowerRankings(data) {
  const rows = data.power_rankings || [];
  if (!rows.length) return card("Power Rankings", null, empty("Not enough games played yet."));

  const best = Math.max(...rows.map((row) => row.power_score), 1);
  const body = rows.map((row) =>
    el(
      "tr",
      {},
      el("td", { class: "num" }, row.rank),
      el("td", {}, movement(row.delta_vs_standings)),
      el("td", { class: "name" }, teamCell(row)),
      el("td", {}, row.record),
      el(
        "td",
        {},
        el(
          "div",
          { class: "bar", title: `${row.power_score} / 100` },
          el("span", {
            class: "bar__fill",
            style: `width:${Math.max(4, (row.power_score / best) * 100)}%`,
          }),
        ),
      ),
      el("td", { class: "num" }, row.power_score.toFixed(1)),
      el("td", { class: "num" }, row.all_play_record),
      el("td", { class: "num" }, points(row.points_per_game)),
      el("td", { class: "num" }, points(row.recent_points_per_game)),
    ),
  );

  return el(
    "div",
    {},
    card(
      "Power Rankings",
      `Through ${rows[0].weeks_counted} completed week${rows[0].weeks_counted === 1 ? "" : "s"}`,
      table(
        [
          { label: "#", align: "right" },
          "Δ",
          "Team",
          "Record",
          "Strength",
          { label: "Score", align: "right" },
          { label: "All-play", align: "right" },
          { label: "PPG", align: "right" },
          { label: "Last 3", align: "right" },
        ],
        body,
      ),
    ),
    methodology(data.weights),
  );
}

// A ranking nobody can interrogate is just an opinion, so show the formula.
function methodology(weights) {
  if (!weights) return null;
  const labels = {
    all_play_win_pct: "All-play win % — how you'd fare against the whole league each week",
    scoring: "Scoring — points per game, normalized across the league",
    recent_form: "Recent form — the same, over the last 3 completed weeks",
    win_pct: "Actual win % — your record",
  };
  return card(
    "How this is calculated",
    null,
    el(
      "ul",
      { class: "muted" },
      Object.entries(weights).map(([key, weight]) =>
        el("li", {}, `${Math.round(weight * 100)}% — ${labels[key] || key}`),
      ),
    ),
    el(
      "p",
      { class: "muted" },
      "Δ compares power rank to standings rank: positive means a team is playing better than " +
        "its record suggests.",
    ),
  );
}

function movement(delta) {
  if (!delta) return el("span", { class: "muted" }, "–");
  const up = delta > 0;
  return el("span", { class: up ? "up" : "down" }, `${up ? "▲" : "▼"}${Math.abs(delta)}`);
}
