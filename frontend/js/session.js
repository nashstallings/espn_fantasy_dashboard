// Session token handling.
//
// The token is a signed claim about a SWID — it carries no ESPN cookie material,
// which is why localStorage is an acceptable home for it. The cookies themselves
// are submitted once and then only ever exist server-side, encrypted.

const TOKEN_KEY = "espn-dashboard.token";
const LEAGUE_KEY = "espn-dashboard.league";

export function saveToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}

export function getToken() {
  return localStorage.getItem(TOKEN_KEY);
}

export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(LEAGUE_KEY);
}

// Remember which league was last open so a reload lands where you left off.
export function saveLeagueChoice(leagueId, season) {
  localStorage.setItem(LEAGUE_KEY, JSON.stringify({ leagueId, season }));
}

export function getLeagueChoice() {
  try {
    const raw = localStorage.getItem(LEAGUE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}
