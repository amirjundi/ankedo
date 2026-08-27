import React, { useEffect, useState } from 'react';
import MetricCard from '../components/MetricCard';
import StatsChart from '../components/StatsChart';
import { Activity, ShieldAlert, ListTree, Database, Inbox } from 'lucide-react';
import { api, ApiError } from '../api';
import './Dashboard.css';

// The stub this replaces reported 42.5 items/second, 120 queued and five healthy
// Facebook accounts, on a system that had never processed anything. Those numbers
// were indistinguishable from real ones on screen, which is the worst property a
// monitoring dashboard can have: it showed a working pipeline while nothing ran.
//
// Everything here is now counted from the database. Where there is nothing, it shows
// zero — which is both the truthful answer and the one that makes a stopped agent
// visible instead of hiding it behind a plausible number.

const TITLE_CASE = (key) => key.charAt(0).toUpperCase() + key.slice(1);

const Dashboard = () => {
  const [health, setHealth] = useState(null);
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const [h, s] = await Promise.all([api.health(), api.summary(7)]);
      setHealth(h);
      setSummary(s);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => {
    load();
    // The agent's own cycle is a minute by default, so anything faster only adds
    // load without adding information.
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);

  const queueChartData = health
    ? ['discovery', 'processing', 'classification', 'review'].map((stage) => ({
        label: TITLE_CASE(stage),
        value: health.queue_depths?.[stage] ?? 0,
      }))
    : [];

  const platformChartData = health
    ? Object.entries(health.account_health || {}).map(([platform, states]) => ({
        label: TITLE_CASE(platform),
        value: Object.values(states).reduce((sum, n) => sum + n, 0),
      }))
    : [];

  const totalAccounts = platformChartData.reduce((sum, p) => sum + p.value, 0);
  const show = (value) => (health || summary ? value : '…');

  return (
    <div className="dashboard animate-fade-in">
      <header className="page-header">
        <h1>System Overview</h1>
        <p className="page-subtitle">
          {health
            ? `${health.status === 'working' ? 'Working' : 'Idle'} · checked ${new Date(
                health.checked_at,
              ).toLocaleTimeString()}`
            : 'Loading live metrics…'}
        </p>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>
          {error}
        </div>
      )}

      {health && !health.platform_configured && (
        <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>
          No platform configured — verdicts stay in this database. Set ETTOK_BASE_URL to
          submit them.
        </div>
      )}

      <section className="metrics-grid">
        <MetricCard
          title="Classified (last hour)"
          value={show(health?.classified_last_hour ?? 0)}
          icon={Activity}
          subtext={
            health?.classifier_latency_ms
              ? `${health.classifier_latency_ms} ms average model call`
              : 'No model calls in the last hour'
          }
        />
        <MetricCard
          title="Items Flagged"
          value={show(summary?.items_flagged_count ?? 0)}
          icon={ShieldAlert}
          subtext="Last 7 days"
        />
        <MetricCard
          title="Review Backlog"
          value={show(health?.queue_depths?.review ?? 0)}
          icon={ListTree}
          subtext="Pending human decisions"
        />
        <MetricCard
          title="Awaiting Submission"
          value={show(health?.outbox?.pending ?? 0)}
          icon={Inbox}
          subtext={
            health?.outbox?.verdicts_held
              ? 'Verdicts held — platform endpoint not live yet'
              : health?.outbox?.failed
                ? `${health.outbox.failed} failed permanently`
                : 'Queued for the platform'
          }
        />
        <MetricCard
          title="Tracked Accounts"
          value={show(totalAccounts)}
          icon={Database}
          subtext={
            platformChartData.length
              ? `Across ${platformChartData.length} platform${platformChartData.length > 1 ? 's' : ''}`
              : 'None added yet'
          }
        />
      </section>

      <div className="dashboard-charts-row">
        <section className="dashboard-chart glass-panel">
          <StatsChart
            title="Pipeline Queue Depths"
            data={queueChartData}
            colorFn={(val, i) =>
              ['hsl(199, 89%, 48%)', 'hsl(221, 83%, 53%)', 'hsl(35, 92%, 53%)', 'hsl(348, 83%, 47%)'][i]
            }
          />
        </section>
        <section className="dashboard-chart glass-panel">
          {platformChartData.length ? (
            <StatsChart
              title="Tracked Accounts by Platform"
              data={platformChartData}
              colorFn={(val, i) =>
                ['hsl(221, 83%, 53%)', 'hsl(348, 83%, 47%)', 'hsl(271, 76%, 53%)', 'hsl(160, 70%, 40%)'][
                  i % 4
                ]
              }
            />
          ) : (
            <div style={{ padding: '2rem', opacity: 0.7 }}>
              <h3>Tracked Accounts by Platform</h3>
              <p>No accounts are being tracked yet.</p>
            </div>
          )}
        </section>
      </div>
    </div>
  );
};

export default Dashboard;
