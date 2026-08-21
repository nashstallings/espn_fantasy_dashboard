// Application controller: boot, routing between tabs, and the connect flow.

import { ApiError, api } from "./api.js";
import { clear, el, empty } from "./dom.js";
import {
  clearToken,
  getLeagueChoice,
  getToken,
  saveLeagueChoice,
  saveToken,
} from "./session.js";
import { renderMatchups } from "./views/matchups.js";
import { renderOverview } from "./views/overview.js";
import { renderPowerRankings } from "./views/power.js";
import { renderRosters } from "./views/rosters.js";
import { renderStandings } from "./views/standings.js";
import { renderTransactions } from "./views/transactions.js";

const dom = {
  app: document.getElementById("app"),
  connect: document.getElementById("connect"),
  connectForm: document.getElementById("connect-form"),
  connectStatus: document.getElementById("connect-status"),
  connectSubmit: document.getElementById("connect-submit"),
  leagueSelect: document.getElementById("league-select"),
  tabs: document.getElementById("tabs"),
  view: document.getElementById("view"),
  footerNote: document.getElementById("footer-note"),
  refresh: document.getElementById("refresh"),
  accountButton: document.getElementById("account"),
  accountDialog: document.getElementById("account-dialog"),
  accountDetails: document.getElementById("account-details"),
  accountStatus: document.getElementById("account-status"),
  addLeagueId: document.getElementById("add-league-id"),
  addLeagueSeason: document.getElementById("add-league-season"),
  addLeague: document.getElementById("add-league"),
  disconnect: document.getElementById("disconnect"),
};

const state = {
  account: null,
  league: null, // { league_id, season, ... }
  view: "overview",
  week: null,
  teamId: null,
  teams: [],
  // Bumped on every load; a slow response for a league you already switched
  // away from is discarded rather than painted over the new one.
  requestId: 0,
};

// --- boot --------------------------------------------------------------------

async function boot() {
  if (!getToken()) return showConnect();
  try {
    state.account = await api.me();
    showDashboard();
  } catch (error) {
    showConnect(error instanceof ApiError ? error.message : "Please reconnect.");
  }
}

function showConnect(message = "") {
  clearToken();
  dom.app.hidden = true;
  dom.connect.hidden = false;
  setStatus(dom.connectStatus, message, message ? "error" : null);
}

function showDashboard() {
  dom.connect.hidden = true;
  dom.app.hidden = false;
  populateLeagues();
  renderTabs();
  loadView();
}

// --- connect -----------------------------------------------------------------

dom.connectForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const swid = document.getElementById("swid").value.trim();
  const espnS2 = document.getElementById("espn_s2").value.trim();
  const leagueId = document.getElementById("league_id").value.trim();
  if (!swid || !espnS2) {
    return setStatus(dom.connectStatus, "Both cookie values are required.", "error");
  }

  dom.connectSubmit.disabled = true;
  setStatus(dom.connectStatus, "Checking your cookies with ESPN…", "busy");
  try {
    const result = await api.connect({
      swid,
      espn_s2: espnS2,
      league_id: leagueId || undefined,
    });
    saveToken(result.token);
    // Clear the pasted secret from the DOM the moment it is no longer needed.
    dom.connectForm.reset();
    state.account = { swid: result.swid, leagues: result.leagues };
    if (result.message) setStatus(dom.connectStatus, result.message, "ok");
    showDashboard();
  } catch (error) {
    setStatus(dom.connectStatus, describe(error), "error");
  } finally {
    dom.connectSubmit.disabled = false;
  }
});

// --- league switching --------------------------------------------------------

function populateLeagues() {
  const leagues = state.account?.leagues || [];
  clear(dom.leagueSelect);

  if (!leagues.length) {
    dom.leagueSelect.append(el("option", { value: "" }, "No leagues linked"));
    dom.leagueSelect.disabled = true;
    state.league = null;
    return;
  }

  dom.leagueSelect.disabled = false;
  for (const league of leagues) {
    dom.leagueSelect.append(
      el(
        "option",
        { value: leagueKey(league) },
        `${league.name || `League ${league.league_id}`} · ${league.season}`,
      ),
    );
  }

  const remembered = getLeagueChoice();
  const match =
    leagues.find(
      (league) =>
        remembered &&
        league.league_id === remembered.leagueId &&
        league.season === remembered.season,
    ) || leagues[0];
  state.league = match;
  dom.leagueSelect.value = leagueKey(match);
}

dom.leagueSelect.addEventListener("change", () => {
  const [season, leagueId] = dom.leagueSelect.value.split("|");
  state.league = (state.account?.leagues || []).find(
    (league) => league.league_id === leagueId && String(league.season) === season,
  );
  if (!state.league) return;
  saveLeagueChoice(state.league.league_id, state.league.season);
  // League-scoped selections do not carry across leagues.
  state.week = null;
  state.teamId = state.league.team_id ?? null;
  state.teams = [];
  loadView();
});

function leagueKey(league) {
  return `${league.season}|${league.league_id}`;
}

// --- tabs --------------------------------------------------------------------

function renderTabs() {
  for (const tab of dom.tabs.querySelectorAll(".tab")) {
    tab.setAttribute("aria-selected", String(tab.dataset.view === state.view));
  }
}

dom.tabs.addEventListener("click", (event) => {
  const tab = event.target.closest(".tab");
  if (!tab) return;
  state.view = tab.dataset.view;
  renderTabs();
  loadView();
});

dom.refresh.addEventListener("click", () => loadView());

// --- view loading ------------------------------------------------------------

async function loadView() {
  renderTabs();
  if (!state.league) {
    clear(dom.view).append(
      empty("No leagues linked yet. Open Account to add one by its league ID."),
    );
    return;
  }

  const requestId = ++state.requestId;
  clear(dom.view).append(empty("Loading…"));
  const { league_id: leagueId, season } = state.league;

  try {
    const node = await buildView(leagueId, season);
    if (requestId !== state.requestId) return; // superseded by a newer load
    clear(dom.view).append(node);
    dom.footerNote.textContent = `${state.league.name || leagueId} · season ${season} · live from ESPN`;
  } catch (error) {
    if (requestId !== state.requestId) return;
    if (error instanceof ApiError && error.needsReconnect) {
      return showConnect(describe(error));
    }
    clear(dom.view).append(errorCard(error));
  }
}

async function buildView(leagueId, season) {
  switch (state.view) {
    case "standings":
      return renderStandings(await api.standings(leagueId, season));

    case "matchups": {
      const data = await api.matchups(leagueId, season, state.week ?? undefined);
      state.week = data.week;
      return renderMatchups(data, {
        onWeekChange: (week) => {
          state.week = week;
          loadView();
        },
      });
    }

    case "rosters": {
      if (!state.teams.length) {
        const teamsData = await api.teams(leagueId, season);
        state.teams = teamsData.teams || [];
      }
      if (!state.teams.length) return empty("This league has no teams yet.");
      if (!state.teams.some((team) => team.team_id === state.teamId)) {
        // Default to the signed-in user's own team where we know it.
        state.teamId = state.league.team_id ?? state.teams[0].team_id;
      }
      const data = await api.roster(leagueId, season, state.teamId, state.week ?? undefined);
      state.week = data.week;
      return renderRosters(data, {
        teams: state.teams,
        selectedTeamId: state.teamId,
        onTeamChange: (teamId) => {
          state.teamId = teamId;
          loadView();
        },
        onWeekChange: (week) => {
          state.week = week;
          loadView();
        },
      });
    }

    case "transactions":
      return renderTransactions(await api.transactions(leagueId, season));

    case "power":
      return renderPowerRankings(await api.powerRankings(leagueId, season));

    default:
      return renderOverview(await api.overview(leagueId, season));
  }
}

function errorCard(error) {
  return el(
    "div",
    { class: "card" },
    el("h2", { class: "card__title" }, "Could not load this view"),
    el("p", { class: "muted" }, describe(error)),
    el("button", { class: "button", onclick: () => loadView() }, "Try again"),
  );
}

// --- account dialog ----------------------------------------------------------

dom.accountButton.addEventListener("click", async () => {
  setStatus(dom.accountStatus, "");
  dom.addLeagueSeason.value = state.league?.season || new Date().getFullYear();
  clear(dom.accountDetails);
  const account = state.account || {};
  appendKeyValue("ESPN member (SWID)", account.swid || "-");
  appendKeyValue("Connected", formatIso(account.connected_at));
  appendKeyValue("Last validated", formatIso(account.last_validated_at));
  appendKeyValue("Leagues linked", String((account.leagues || []).length));
  dom.accountDialog.showModal();
});

function appendKeyValue(label, value) {
  dom.accountDetails.append(el("dt", {}, label), el("dd", {}, value));
}

dom.addLeague.addEventListener("click", async () => {
  const leagueId = dom.addLeagueId.value.trim();
  const season = Number(dom.addLeagueSeason.value) || undefined;
  if (!leagueId) return setStatus(dom.accountStatus, "Enter a league ID.", "error");

  dom.addLeague.disabled = true;
  setStatus(dom.accountStatus, "Checking that league with ESPN…", "busy");
  try {
    await api.addLeague(leagueId, season);
    state.account = await api.me();
    populateLeagues();
    dom.addLeagueId.value = "";
    setStatus(dom.accountStatus, "League added.", "ok");
    loadView();
  } catch (error) {
    setStatus(dom.accountStatus, describe(error), "error");
  } finally {
    dom.addLeague.disabled = false;
  }
});

dom.disconnect.addEventListener("click", async () => {
  const confirmed = window.confirm(
    "This deletes your stored ESPN cookies and league list from the server. Continue?",
  );
  if (!confirmed) return;
  try {
    await api.disconnect();
  } catch {
    // Deleting locally matters more than the server's answer here.
  }
  state.account = null;
  state.league = null;
  dom.accountDialog.close();
  showConnect("Disconnected. Your stored credentials were deleted.");
});

// --- helpers -----------------------------------------------------------------

function setStatus(node, message, kind) {
  node.textContent = message || "";
  node.className = `status${kind ? ` status--${kind}` : ""}`;
}

function describe(error) {
  if (error instanceof ApiError) {
    if (error.code === "ESPNAuthError") {
      return "ESPN rejected the stored cookies. They expire when you log out of ESPN — reconnect with fresh values.";
    }
    if (error.code === "network_error") {
      return "Could not reach the dashboard API. Check that API_BASE in config.js points at your Cloud Run service.";
    }
    return error.message;
  }
  return error?.message || "Something went wrong.";
}

function formatIso(value) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

boot();
