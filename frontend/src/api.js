// The dashboard's one way of talking to the agent.
//
// Every page but Chat returned hardcoded arrays behind a setTimeout, so this did not
// exist and the token handling lived only in Chat.jsx. Extracted rather than copied
// nine times, because the auth rule — omit the header when there is no token — is the
// kind of thing that gets subtly wrong in the ninth copy.

const BASE = ''; // same origin: the dashboard is served by the agent itself

// localStorage, so the token is entered once and survives closing the tab.
//
// This was sessionStorage, on the reasoning that a token should not sit on disk on a
// machine holding evidence about people at risk. That reasoning was wrong about where
// the risk is. Retyping a 32-character token on every tab is the kind of friction
// people solve by picking a short token, writing it on a note, or turning auth off —
// each worse than the disk write it was avoiding. And the token is already on disk on
// that machine, in .env, in plaintext.
//
// It is scoped to the dashboard's own origin, so a tunnel domain and localhost keep
// separate tokens, which is the correct behaviour when they may not be the same agent.
//
// clear() exists for a shared or borrowed machine, and is what the UI offers on a
// rejected token — a stored bad token would otherwise fail silently forever.
const KEY = 'ankedo_token';

export const getToken = () => {
  try {
    return localStorage.getItem(KEY) || '';
  } catch {
    // Private browsing and some hardened configurations throw on access rather than
    // returning null. An unreadable store is an absent one, not a crashed dashboard.
    return '';
  }
};

export const setToken = (value) => {
  try {
    localStorage.setItem(KEY, value.trim());
  } catch {
    /* nothing to do: the request still carries the token for this page load */
  }
};

export const clearToken = () => {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* already unreachable */
  }
};

export const hasToken = () => Boolean(getToken());

/** Raised for anything the caller may want to distinguish; `status` is 0 for a
 *  transport failure, where no response ever arrived. */
export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.status = status;
  }
}

async function request(path, { method = 'GET', body } = {}) {
  const token = getToken();

  let res;
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      headers: {
        ...(body ? { 'Content-Type': 'application/json' } : {}),
        // "Bearer " with an empty token is an illegal header value: the browser
        // rejects the request before it leaves and reports "Failed to fetch", which
        // reads exactly like the agent being down.
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      ...(body ? { body: JSON.stringify(body) } : {}),
    });
  } catch (err) {
    throw new ApiError(`Cannot reach the agent: ${err.message}`, 0);
  }

  if (res.status === 401 || res.status === 403) {
    // Drop it. A stored token the agent rejects would otherwise be resent on every
    // request forever, and the prompt to replace it would never be reachable.
    if (token) clearToken();
    throw new ApiError(
      token ? 'Token rejected — check ADMIN_API_TOKEN.' : 'Sign in: paste your admin token.',
      res.status,
    );
  }
  if (res.status === 503) {
    throw new ApiError('The agent has no ADMIN_API_TOKEN set. Run `ankedo setup`.', 503);
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const parsed = await res.json();
      if (parsed?.detail) detail = parsed.detail;
    } catch { /* keep the status */ }
    throw new ApiError(detail, res.status);
  }

  return res.status === 204 ? null : res.json();
}

export const api = {
  reviewQueue: () => request('/api/review/queue'),
  submitReview: (queueItemId, payload) =>
    request(`/api/review/${queueItemId}/submit`, { method: 'POST', body: payload }),

  notifications: () => request('/api/notifications/'),
  respondToNotification: (id, payload) =>
    request(`/api/notifications/${id}/respond`, { method: 'POST', body: payload }),

  health: () => request('/api/admin/health'),
  summary: (days = 30) => request(`/api/reports/summary?days=${days}`),
  repeatOffenders: () => request('/api/reports/repeat-offenders'),
  pageStats: () => request('/api/reports/stats/pages'),

  cases: () => request('/api/cases'),
  targetGroups: () => request('/api/target-groups'),
  createCase: (payload) => request('/api/cases', { method: 'POST', body: payload }),
  evidence: (limit = 50) => request(`/api/evidence?limit=${limit}`),
  offenders: () => request('/api/intelligence/offenders'),
  trends: () => request('/api/intelligence/trends'),

  config: () => request('/api/admin/config'),
  setConfig: (key, value) => request('/api/admin/config', { method: 'PATCH', body: { key, value } }),
  backup: (destination_path) =>
    request('/api/admin/backup', { method: 'POST', body: { destination_path } }),

  accounts: () => request('/api/accounts/'),
  chat: (payload) => request('/api/chat', { method: 'POST', body: payload }),
};
