import React, { useState, useEffect } from 'react';
import EvidenceViewer from '../components/EvidenceViewer';
import StatusBadge from '../components/StatusBadge';
import { Image, Search, Calendar, Download } from 'lucide-react';
import { api, ApiError } from '../api';
import './Evidence.css';

const Evidence = () => {
  const [packages, setPackages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');

  const [error, setError] = useState(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const data = await api.evidence();
        setPackages(data.evidence || []);
        setError(null);
      } catch (err) {
        setError(err instanceof ApiError ? err.message : String(err));
      } finally {
        setLoaded(true);
      }
    })();
  }, []);

  const filtered = packages.filter(p => {
    const matchPlatform = platformFilter === 'all' || p.platform === platformFilter;
    const needle = searchTerm.toLowerCase();
    const matchSearch = !searchTerm ||
      p.excerpt?.toLowerCase().includes(needle) ||
      p.trope_fired?.toLowerCase().includes(needle) ||
      p.reviewer_id?.toLowerCase().includes(needle);
    return matchPlatform && matchSearch;
  });

  return (
    <div className="evidence-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Evidence Packages</h1>
          <p className="page-subtitle">Confirmed hate speech evidence — screenshots, classifications, and audit trails</p>
        </div>
      </header>

      {error && <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{error}</div>}

      <div className="evidence-toolbar">
        <div className="search-bar">
          <Search size={18} className="search-icon" />
          <input
            type="text"
            placeholder="Search text, trope, or reviewer…"
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
        <div className="platform-filters">
          {['all', 'facebook', 'tiktok', 'instagram'].map(p => (
            <button
              key={p}
              className={`filter-btn ${platformFilter === p ? 'active' : ''}`}
              onClick={() => setPlatformFilter(p)}
            >
              {p === 'all' ? 'All Platforms' : p}
            </button>
          ))}
        </div>
      </div>

      <div className="evidence-layout">
        <div className="evidence-list">
          {filtered.map(pkg => (
            <div
              key={pkg.id}
              className={`evidence-item glass-panel ${selected?.id === pkg.id ? 'selected' : ''}`}
              onClick={() => setSelected(pkg)}
            >
              <div className="ev-item-top">
                <span className={`platform-badge ${pkg.platform || 'unknown'} sm`}>
                  {pkg.platform || 'unknown'}
                </span>
                {pkg.has_screenshot && <StatusBadge status="sealed" size="sm" />}
              </div>
              {/* The text that was judged. The stub showed a classification label
                  instead; the excerpt is the thing a reviewer actually needs to see,
                  and it is what the package is evidence of. */}
              <h4 className="ev-item-classification" dir="auto">{pkg.excerpt || '(no text)'}</h4>
              {pkg.trope_fired && (
                <div className="ev-item-meta">
                  <span className="ev-item-group">{pkg.trope_fired}</span>
                </div>
              )}
              <div className="ev-item-footer">
                <Calendar size={12} />
                <span>
                  {pkg.confirmed_at ? new Date(pkg.confirmed_at).toLocaleDateString() : '—'}
                </span>
                <span className="ev-item-reviewer">by {pkg.reviewer_id}</span>
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="evidence-empty">
              <Image size={40} />
              <p>
                {loaded
                  ? 'No evidence packages yet. One is sealed each time a reviewer confirms a verdict.'
                  : 'Loading…'}
              </p>
            </div>
          )}
        </div>

        <div className="evidence-detail-panel">
          {selected ? (
            <EvidenceViewer evidence={selected} onClose={() => setSelected(null)} />
          ) : (
            <div className="evidence-detail-empty glass-panel">
              <Image size={40} />
              <p>Select an evidence package to view details</p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default Evidence;
