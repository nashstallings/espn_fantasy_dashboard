import { card, el, empty, points, table } from "../dom.js";

export function renderRosters(data, { teams, selectedTeamId, onTeamChange, onWeekChange } = {}) {
  const roster = data.roster;
  const league = data.league || {};
  const week = data.week || league.current_week;

  const teamPicker = el(
    "select",
    { "aria-label": "Team", onchange: (e) => onTeamChange?.(Number(e.target.value)) },
    (teams || []).map((team) =>
      el(
        "option",
        { value: team.team_id, selected: team.team_id === selectedTeamId || null },
        team.name,
      ),
    ),
  );

  const weekPicker = el(
    "select",
    { "aria-label": "Week", onchange: (e) => onWeekChange?.(Number(e.target.value)) },
    Array.from({ length: Math.max(league.latest_scoring_period || 1, 1) }, (_, i) =>
      el("option", { value: i + 1, selected: i + 1 === week || null }, `Week ${i + 1}`),
    ),
  );

  const controls = el("div", { class: "card__head" }, teamPicker, weekPicker);
  if (!roster) return card("Rosters", null, controls, empty("No roster for this team."));

  return card(
    roster.name,
    `Starters: ${points(roster.starter_points)} pts · projected ${points(
      roster.starter_projected_points,
    )}`,
    controls,
    playerTable("Starters", roster.starters),
    playerTable("Bench", roster.bench),
  );
}

function playerTable(label, players) {
  if (!players?.length) return el("p", { class: "muted" }, `No ${label.toLowerCase()}.`);
  const rows = players.map((player) =>
    el(
      "tr",
      {},
      el("td", {}, el("span", { class: "pill" }, player.slot)),
      el(
        "td",
        { class: "name" },
        el("span", { class: "team__name" }, player.name),
        player.injury_status && player.injury_status !== "ACTIVE"
          ? el("span", { class: "team__owner" }, player.injury_status)
          : null,
      ),
      el("td", {}, player.position),
      el("td", {}, player.pro_team),
      el("td", { class: "num" }, points(player.points)),
      el("td", { class: "num muted" }, points(player.projected_points)),
      el("td", { class: "num" }, points(player.season_points)),
    ),
  );
  return el(
    "div",
    {},
    el("h3", { class: "dialog__subtitle" }, label),
    table(
      [
        "Slot",
        "Player",
        "Pos",
        "Team",
        { label: "Pts", align: "right" },
        { label: "Proj", align: "right" },
        { label: "Season", align: "right" },
      ],
      rows,
    ),
  );
}
