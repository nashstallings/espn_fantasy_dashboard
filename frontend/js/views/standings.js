import { card, el, empty, points, table, teamCell } from "../dom.js";

export function renderStandings(data) {
  const rows = data.standings || [];
  if (!rows.length) return empty("No standings yet — this league has not played a week.");

  const body = rows.map((row) =>
    el(
      "tr",
      {},
      el("td", { class: "num" }, row.rank),
      el("td", { class: "name" }, teamCell(row)),
      el("td", {}, recordText(row)),
      el("td", { class: "num" }, points(row.points_for)),
      el("td", { class: "num" }, points(row.points_against)),
      el("td", { class: "num" }, points(row.points_for_avg)),
      el("td", {}, streakPill(row.streak)),
    ),
  );

  const playoffCut = data.league?.playoff_team_count;
  return card(
    "Standings",
    playoffCut ? `Top ${playoffCut} make the playoffs` : null,
    table(
      [
        { label: "#", align: "right" },
        "Team",
        "Record",
        { label: "PF", align: "right" },
        { label: "PA", align: "right" },
        { label: "PPG", align: "right" },
        "Streak",
      ],
      body,
    ),
  );
}

export function recordText(row) {
  const base = `${row.wins}-${row.losses}`;
  return row.ties ? `${base}-${row.ties}` : base;
}

function streakPill(streak) {
  if (!streak || streak === "-") return el("span", { class: "muted" }, "-");
  const kind = streak.startsWith("W") ? "pill--good" : streak.startsWith("L") ? "pill--bad" : "";
  return el("span", { class: `pill ${kind}` }, streak);
}
