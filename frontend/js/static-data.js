// Static data source.
//
// The site is built by a scheduled GitHub Action into a tree of JSON files, so
// there is no API to call — just files to fetch. This module presents the same
// shapes the views already consume, so nothing in views/ had to change.
//
// Everything is fetched once and reused: the whole dataset is a few hundred KB
// and it does not change between builds, so re-fetching per tab switch would
// only add latency.

const BASE = (window.APP_CONFIG?.DATA_BASE || "./data").replace(/\/$/, "");

export class DataError extends Error {
  constructor(message, { missing = false } = {}) {
    super(message);
    this.missing = missing;
  }
}

const cache = new Map();

async function load(name) {
  if (cache.has(name)) return cache.get(name);

  let response;
  try {
    response = await fetch(`${BASE}/${name}.json`, { cache: "no-cache" });
  } catch {
    throw new DataError("Could not load the dashboard data files.");
  }
  if (response.status === 404) {
    throw new DataError(`No data for ${name} in this build.`, { missing: true });
  }
  if (!response.ok) {
    throw new DataError(`Data file ${name}.json returned ${response.status}.`);
  }

  let payload;
  try {
    payload = await response.json();
  } catch {
    throw new DataError(`Data file ${name}.json is not valid JSON.`);
  }
  cache.set(name, payload);
  return payload;
}

export const staticData = {
  // Build manifest: what this build actually contains.
  index: () => load("index"),
  overview: () => load("overview"),
  standings: () => load("standings"),
  teams: () => load("teams"),
  transactions: () => load("transactions"),
  powerRankings: () => load("power-rankings"),

  // Every week ships in one file; slicing here keeps the views unchanged.
  async matchups(week) {
    const data = await load("matchups");
    const target = week || data.league?.current_week || 1;
    return {
      league: data.league,
      week: target,
      matchups: (data.matchups || []).filter((game) => game.week === target),
      generated_at: data.generated_at,
    };
  },

  // Rosters are one file per scoring period, each holding every team.
  async roster(teamId, week) {
    const manifest = await load("index");
    const weeks = manifest.roster_weeks || [];
    if (!weeks.length) throw new DataError("This build has no roster data.", { missing: true });

    const target = weeks.includes(week) ? week : weeks[weeks.length - 1];
    const data = await load(`rosters/week-${target}`);
    const roster = (data.rosters || []).find((entry) => entry.team_id === teamId);
    if (!roster) throw new DataError(`No roster for team ${teamId} in week ${target}.`);
    return { league: data.league, week: target, roster, generated_at: data.generated_at };
  },
};
