// The dashboard's one way of talking to the agent.
//
// Every page but Chat returned hardcoded arrays behind a setTimeout, so this did not
// exist and the token handling lived only in Chat.jsx. Extracted rather than copied
// nine times, because the auth rule — omit the header when there is no token — is the
// kind of thing that gets subtly wrong in the ninth copy.

const BASE = ''; // same origin: the dashboard is served by the agent itself

// sessionStorage, not localStorage: the token dies with the tab rather than sitting
// on disk on a machine holding evidence about people at risk.
export const getToken = () => sessionStorage.getItem('ankedo_token') || '';
export const setToken = (value) => sessionStorage.setItem('ankedo_token', value.trim());
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

  accounts: () => request('/api/accounts/'),
  chat: (payload) => request('/api/chat', { method: 'POST', body: payload }),
};
