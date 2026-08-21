import { card, el, empty, points, teamCell } from "../dom.js";

export function renderMatchups(data, { onWeekChange } = {}) {
  const league = data.league || {};
  const week = data.week || league.current_week || 1;
  const games = data.matchups || [];

  const picker = el(
    "select",
    {
      "aria-label": "Week",
      onchange: (event) => onWeekChange?.(Number(event.target.value)),
    },
    weekOptions(league, week),
  );

  const body = games.length
    ? el("div", { class: "grid" }, games.map((game) => matchupCard(game)))
    : empty("ESPN has no matchups for this week.");

  return card(`Week ${week}`, null, el("div", { class: "card__head" }, picker), body);
}

function weekOptions(league, selected) {
  // Prefer the league's scheduled length, but never offer fewer weeks than have
  // already been played (a league mid-restructure can report a stale count).
  const total = Math.max(league.final_week || 0, league.current_week || 0, 1);
  return Array.from({ length: total }, (_, index) => {
    const week = index + 1;
    return el("option", { value: week, selected: week === selected || null }, `Week ${week}`);
  });
}

function matchupCard(game) {
  const homeWon = game.winner === "HOME";
  const awayWon = game.winner === "AWAY";
  return el(
    "article",
    { class: "matchup" },
    el(
      "div",
      { class: "matchup__head" },
      el("span", {}, game.is_complete ? "Final" : "In progress"),
      game.playoff_tier && game.playoff_tier !== "NONE"
        ? el("span", { class: "pill pill--accent" }, prettyTier(game.playoff_tier))
        : null,
    ),
    side(game.away, awayWon),
    side(game.home, homeWon),
  );
}

function side(team, isWinner) {
  if (!team) {
    return el(
      "div",
      { class: "matchup__side" },
      el("span", { class: "muted" }, "Bye"),
      el("span", { class: "matchup__score muted" }, "-"),
    );
  }
  return el(
    "div",
    { class: `matchup__side${isWinner ? " matchup__winner" : ""}` },
    teamCell(team),
    el("span", { class: "matchup__score" }, points(team.points)),
  );
}

function prettyTier(tier) {
  return tier.replaceAll("_", " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase());
}
