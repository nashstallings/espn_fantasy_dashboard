// Thin client over the Cloud Run API.
//
// Every failure surfaces as an ApiError carrying the backend's machine-readable
// `error` code, so callers can tell "your ESPN cookies died" (send the user back
// to connect) apart from "ESPN is down" (show a retry).

import { getToken, clearToken } from "./session.js";

const BASE = (window.APP_CONFIG?.API_BASE || "").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(status, code, detail) {
    super(detail || code || `Request failed (${status})`);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }

  // Any of these mean the stored credentials can no longer talk to ESPN.
  get needsReconnect() {
    return (
      this.status === 401 ||
      this.code === "ESPNAuthError" ||
      this.code === "not_connected" ||
      this.code === "credential_unreadable"
    );
  }
}

async function request(path, { method = "GET", body, auth = true } = {}) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (auth) {
    const token = getToken();
    if (!token) throw new ApiError(401, "not_connected", "No session. Connect your ESPN account.");
    headers.Authorization = `Bearer ${token}`;
  }

  let response;
  try {
    response = await fetch(`${BASE}${path}`, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (cause) {
    throw new ApiError(0, "network_error", "Could not reach the dashboard API.");
  }

  if (response.status === 204) return null;

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const code = payload?.error || String(response.status);
    const detail = payload?.detail || payload?.message || describeStatus(response.status);
    const error = new ApiError(response.status, code, detail);
    if (error.needsReconnect && code !== "league_not_linked") clearToken();
    throw error;
  }
  return payload;
}

function describeStatus(status) {
  if (status === 403) return "This league is not linked to your account.";
  if (status === 404) return "ESPN has no record of that league or team.";
  if (status === 503) return "ESPN is not responding right now. Try again shortly.";
  return `Request failed (${status}).`;
}

export const api = {
  connect: (payload) => request("/api/connect", { method: "POST", body: payload, auth: false }),
  me: () => request("/api/me"),
  season: () => request("/api/season", { auth: false }),
  addLeague: (leagueId, season) =>
    request("/api/me/leagues", { method: "POST", body: { league_id: leagueId, season } }),
  removeLeague: (leagueId, season) =>
    request(`/api/me/leagues/${season}/${encodeURIComponent(leagueId)}`, { method: "DELETE" }),
  disconnect: () => request("/api/me", { method: "DELETE" }),

  overview: (leagueId, season) => request(leagueUrl(leagueId, "overview", { season })),
  standings: (leagueId, season) => request(leagueUrl(leagueId, "standings", { season })),
  matchups: (leagueId, season, week) => request(leagueUrl(leagueId, "matchups", { season, week })),
  teams: (leagueId, season) => request(leagueUrl(leagueId, "teams", { season })),
  roster: (leagueId, season, teamId, week) =>
    request(leagueUrl(leagueId, `teams/${teamId}/roster`, { season, week })),
  transactions: (leagueId, season, limit = 100) =>
    request(leagueUrl(leagueId, "transactions", { season, limit })),
  powerRankings: (leagueId, season) => request(leagueUrl(leagueId, "power-rankings", { season })),
};

function leagueUrl(leagueId, path, params = {}) {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, value);
  }
  const suffix = query.toString();
  return `/api/leagues/${encodeURIComponent(leagueId)}/${path}${suffix ? `?${suffix}` : ""}`;
}
