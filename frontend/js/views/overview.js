import { card, el, empty, points, table, teamCell } from "../dom.js";
import { recordText } from "./standings.js";

export function renderOverview(data) {
  const league = data.league || {};
  const standings = data.standings || [];
  const rankings = data.power_rankings || [];
  const games = data.current_matchups || [];

  return el(
    "div",
    {},
    el(
      "div",
      { class: "grid" },
      leaderCard("Standings leader", standings[0], (row) => `${recordText(row)} · ${points(row.points_for)} PF`),
      leaderCard("Power ranking leader", rankings[0], (row) => `Power score ${row.power_score}`),
      leaderCard("Most points", topScorer(standings), (row) => `${points(row.points_for)} points`),
    ),
    card(
      `Week ${league.current_week ?? "-"}`,
      games.length ? null : "No matchups scheduled",
      games.length
        ? table(
            ["Away", { label: "Pts", align: "right" }, "Home", { label: "Pts", align: "right" }],
            games.map((game) =>
              el(
                "tr",
                {},
                el("td", { class: "name" }, game.away ? teamCell(game.away) : "Bye"),
                el("td", { class: "num" }, game.away ? points(game.away.points) : "-"),
                el("td", { class: "name" }, game.home ? teamCell(game.home) : "Bye"),
                el("td", { class: "num" }, game.home ? points(game.home.points) : "-"),
              ),
            ),
          )
        : empty("Nothing scheduled this week."),
    ),
  );
}

function leaderCard(title, row, subtitle) {
  if (!row) return card(title, null, empty("Not enough data yet."));
  return card(title, null, teamCell(row), el("p", { class: "muted" }, subtitle(row)));
}

function topScorer(standings) {
  return standings.reduce(
    (best, row) => (!best || row.points_for > best.points_for ? row : best),
    null,
  );
}
