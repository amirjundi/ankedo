import React, { useEffect, useState } from 'react';
import MetricCard from '../components/MetricCard';
import StatsChart from '../components/StatsChart';
import { Activity, ShieldAlert, ListTree, Database, TrendingUp } from 'lucide-react';
import './Dashboard.css';

const Dashboard = () => {
  const [health, setHealth] = useState(null);
  const [summary, setSummary] = useState(null);

  useEffect(() => {
    // Would fetch from /api/admin/health and /api/reports/summary
    setTimeout(() => {
      setHealth({
        crawl_throughput: 42.5,
        queue_depths: { review: 5, processing: 45, classification: 12, discovery: 120 },
        account_health: { facebook: { active: 5 }, tiktok: { active: 3 }, instagram: { active: 4 } }
      });
      setSummary({
        new_cases_count: 14,
        items_flagged_count: 89
      });
    }, 500);
  }, []);

  const queueChartData = health ? [
    { label: 'Discovery', value: health.queue_depths.discovery },
    { label: 'Processing', value: health.queue_depths.processing },
    { label: 'Classification', value: health.queue_depths.classification },
    { label: 'Review', value: health.queue_depths.review },
  ] : [];

  const platformChartData = health ? [
    { label: 'Facebook', value: health.account_health.facebook.active },
    { label: 'TikTok', value: health.account_health.tiktok.active },
    { label: 'Instagram', value: health.account_health.instagram.active },
  ] : [];

  return (
    <div className="dashboard animate-fade-in">
      <header className="page-header">
        <h1>System Overview</h1>
        <p className="page-subtitle">Real-time health and intelligence metrics</p>
      </header>

      <section className="metrics-grid">
        <MetricCard 
          title="Crawl Throughput" 
          value={health ? `${health.crawl_throughput} /s` : '...'} 
          icon={Activity} 
          trend={12} 
        />
        <MetricCard 
          title="Items Flagged" 
          value={summary ? summary.items_flagged_count : '...'} 
          icon={ShieldAlert} 
          trend={5}
        />
        <MetricCard 
          title="Review Backlog" 
          value={health ? health.queue_depths.review : '...'} 
          icon={ListTree} 
          subtext="Pending human decisions"
        />
        <MetricCard 
          title="Active Crawlers" 
          value={health ? Object.values(health.account_health).reduce((sum, p) => sum + p.active, 0) : '...'} 
          icon={Database} 
          subtext="Across 3 platforms"
        />
      </section>

      <div className="dashboard-charts-row">
        <section className="dashboard-chart glass-panel">
          <StatsChart
            title="Pipeline Queue Depths"
            data={queueChartData}
            colorFn={(val, i) => {
              const colors = ['hsl(199, 89%, 48%)', 'hsl(221, 83%, 53%)', 'hsl(35, 92%, 53%)', 'hsl(348, 83%, 47%)'];
              return colors[i];
            }}
          />
        </section>
        <section className="dashboard-chart glass-panel">
          <StatsChart
            title="Active Accounts by Platform"
            data={platformChartData}
            colorFn={(val, i) => {
              const colors = ['hsl(221, 83%, 53%)', 'hsl(348, 83%, 47%)', 'hsl(271, 76%, 53%)'];
              return colors[i];
            }}
          />
        </section>
      </div>
    </div>
  );
};

export default Dashboard;
