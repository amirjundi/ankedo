import React, { useState, useEffect } from 'react';
import { Search, AlertTriangle, TrendingUp } from 'lucide-react';
import { api, ApiError } from '../api';
import './Intelligence.css';

// This page listed `@angry_user99`, `@hate_speech_bot` and `@troll_farm_alpha` with
// offence counts and a "Banned (Linked)" status, under the heading "Repeat Offenders
// Watchlist". None of it existed. A page that names accounts and counts their offences
// is an accusation, and it must never show anything the database cannot support.
//
// "Confirmed Offenses" is also gone as a column title. These are flagged items — the
// agent's own verdicts — and calling them confirmed claims a human agreed when no one
// has. The word `Flagged` is the honest one.

const Intelligence = () => {
  const [offenders, setOffenders] = useState([]);
  const [trends, setTrends] = useState([]);
  const [query, setQuery] = useState('');
  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const [o, t] = await Promise.all([api.offenders(), api.trends()]);
        setOffenders(o.offenders || []);
        setTrends(t.trends || []);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const filtered = query
    ? offenders.filter((a) => (a.handle || '').toLowerCase().includes(query.toLowerCase()))
    : offenders;

  const spikes = trends.filter((t) => t.observed);

  return (
    <div className="intelligence animate-fade-in">
      <header className="page-header flex justify-between items-center">
        <div>
          <h1>Intelligence Hub</h1>
          <p className="page-subtitle">Accounts with more than one flagged item</p>
        </div>
        <div className="search-bar">
          <Search size={18} className="search-icon" />
          <input
            type="text"
            placeholder="Search handles…"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{error}</div>
      )}

      <div className="intelligence-content glass-panel">
        <div className="table-header">
          <h3>Repeat Offenders</h3>
          {filtered.length > 0 && (
            <span className="badge-warning">
              <AlertTriangle size={14} /> {filtered.length} tracked
            </span>
          )}
        </div>

        {filtered.length === 0 ? (
          <div style={{ padding: '2rem', opacity: 0.7 }}>
            {loaded
              ? 'No account has more than one flagged item. One flagged item is not a pattern.'
              : 'Loading…'}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Handle</th>
                <th>Platform</th>
                <th>Flagged Items</th>
                <th>Last Seen</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map((actor) => (
                <tr key={`${actor.platform}:${actor.handle}`}>
                  <td className="handle">{actor.handle}</td>
                  <td>
                    <span className={`platform-badge ${actor.platform} sm`}>{actor.platform}</span>
                  </td>
                  <td className="offenses">{actor.offenses}</td>
                  <td className="last-active">
                    {actor.last_seen ? new Date(actor.last_seen).toLocaleString() : '—'}
                  </td>
                  <td>
                    {/* Awaiting a per-author timeline endpoint. A button that does
                        nothing is how the rest of this dashboard went wrong. */}
                    <span style={{ opacity: 0.5 }}>—</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="intelligence-content glass-panel" style={{ marginTop: '1.5rem' }}>
        <div className="table-header">
          <h3>Hate Density Signals</h3>
          {spikes.length > 0 && (
            <span className="badge-warning">
              <TrendingUp size={14} /> {spikes.length} spike{spikes.length > 1 ? 's' : ''}
            </span>
          )}
        </div>

        {trends.length === 0 ? (
          <div style={{ padding: '2rem', opacity: 0.7 }}>
            {loaded ? 'No trend signals recorded yet.' : 'Loading…'}
          </div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Target Group</th>
                <th>Platform</th>
                <th>Hour</th>
                <th>Scanned</th>
                <th>Flagged</th>
                <th>Density</th>
              </tr>
            </thead>
            <tbody>
              {trends.map((t) => (
                <tr key={t.id}>
                  <td>{t.target_group}</td>
                  <td>
                    <span className={`platform-badge ${t.platform} sm`}>{t.platform}</span>
                  </td>
                  <td>{t.hour_bucket}</td>
                  <td>{t.items_scanned}</td>
                  <td>{t.items_flagged}</td>
                  <td>{(t.hate_density * 100).toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};

export default Intelligence;
