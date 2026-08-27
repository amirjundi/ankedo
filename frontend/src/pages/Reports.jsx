import React, { useState, useEffect } from 'react';
import MetricCard from '../components/MetricCard';
import StatsChart from '../components/StatsChart';
import { FileText, TrendingUp, AlertTriangle, Calendar, Users } from 'lucide-react';
import { api, ApiError } from '../api';
import './Reports.css';

const Reports = () => {
  const [summary, setSummary] = useState(null);
  const [pageStats, setPageStats] = useState([]);
  const [offenders, setOffenders] = useState([]);
  const [dateRange, setDateRange] = useState(30);

  const [error, setError] = useState(null);

  useEffect(() => {
    (async () => {
      try {
        const [s, ps, off] = await Promise.all([
          api.summary(dateRange),
          api.pageStats(),
          api.repeatOffenders(),
        ]);
        setSummary(s);
        // The chart wants {label, value}; the endpoint returns its own shape.
        setPageStats((ps.pages || ps.stats || []).map((row) => ({
          label: row.handle || row.page || row.label || 'unknown',
          value: row.flagged ?? row.count ?? row.value ?? 0,
        })));
        setOffenders(off.offenders || []);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      }
    })();
  }, [dateRange]);

  return (
    <div className="reports-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Reports</h1>
          <p className="page-subtitle">Summary analytics, per-page statistics, and repeat offender tracking</p>
        </div>
        <div className="date-range-selector">
          <Calendar size={16} />
          <select value={dateRange} onChange={e => setDateRange(Number(e.target.value))}>
            <option value={7}>Last 7 days</option>
            <option value={30}>Last 30 days</option>
            <option value={90}>Last 90 days</option>
            <option value={365}>Last year</option>
          </select>
        </div>
      </header>

      {/* Summary Metrics */}
      <section className="metrics-grid">
        <MetricCard title="New Cases" value={summary?.new_cases_count ?? '...'} icon={FileText} trend={8} />
        <MetricCard title="Items Flagged" value={summary?.items_flagged_count ?? '...'} icon={AlertTriangle} trend={15} />
        <MetricCard title="Confirmed" value={summary?.confirmed_count ?? '...'} icon={TrendingUp} subtext="Human-verified" />
        <MetricCard title="False Positive Rate" value={summary ? `${summary.false_positive_rate}%` : '...'} icon={Users} subtext="Lower is better" trend={-3} />
      </section>

      {/* Charts + Tables */}
      <div className="reports-grid">
        <section className="reports-chart-section glass-panel">
          <StatsChart
            title="Hate Speech by Page (Confirmed Items)"
            data={pageStats}
            colorFn={(val, i) => {
              const colors = ['hsl(348, 83%, 47%)', 'hsl(20, 90%, 50%)', 'hsl(35, 92%, 53%)', 'hsl(221, 83%, 53%)', 'hsl(271, 76%, 53%)', 'hsl(199, 89%, 48%)'];
              return colors[i % colors.length];
            }}
          />
        </section>

        <section className="reports-offenders glass-panel">
          <div className="table-header">
            <h3>Repeat Offenders (Top 5)</h3>
          </div>
          <table className="data-table">
            <thead>
              <tr>
                <th>Handle</th>
                <th>Platform</th>
                <th>Offenses</th>
                <th>Last Active</th>
              </tr>
            </thead>
            <tbody>
              {offenders.map((o, i) => (
                <tr key={i}>
                  <td className="handle">{o.handle}</td>
                  <td><span className={`platform-badge ${o.platform} sm`}>{o.platform}</span></td>
                  <td className="offenses">{o.offenses}</td>
                  <td className="last-active">{o.last_active}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>
    </div>
  );
};

export default Reports;
