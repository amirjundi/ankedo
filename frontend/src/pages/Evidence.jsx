import React, { useState, useEffect } from 'react';
import EvidenceViewer from '../components/EvidenceViewer';
import StatusBadge from '../components/StatusBadge';
import { Image, Search, Calendar, Download } from 'lucide-react';
import './Evidence.css';

const STUB_EVIDENCE = [
  {
    id: 'ev_001', post_id: 'p_123', platform: 'facebook', url: 'https://facebook.com/post/123',
    screenshot_path: null, severity: 'high', classification: 'Dehumanization',
    target_group: 'Yazidi', trope_fired: 'Devil-worship trope (اعوذ بالله من الشيطان الرجيم)',
    reviewer_id: 'reviewer_1', confirmed_at: '2026-07-23T08:12:00Z', case_title: 'Anti-Yazidi Campaign'
  },
  {
    id: 'ev_002', post_id: 'p_456', platform: 'tiktok', url: 'https://tiktok.com/@user/video/456',
    screenshot_path: null, severity: 'critical', classification: 'Incitement to Violence',
    target_group: 'Christian', trope_fired: null,
    reviewer_id: 'reviewer_2', confirmed_at: '2026-07-23T07:45:00Z', case_title: 'Christian Displacement'
  },
  {
    id: 'ev_003', post_id: 'p_789', platform: 'instagram', url: 'https://instagram.com/p/789',
    screenshot_path: null, severity: 'medium', classification: 'Hate Speech - Slur',
    target_group: 'Shabak', trope_fired: null,
    reviewer_id: 'reviewer_1', confirmed_at: '2026-07-22T22:30:00Z', case_title: 'Shabak Community'
  },
  {
    id: 'ev_004', post_id: 'p_101', platform: 'facebook', url: 'https://facebook.com/post/101',
    screenshot_path: null, severity: 'high', classification: 'Coded Hate Speech',
    target_group: 'Mandaean', trope_fired: 'Impurity trope — "نجس"',
    reviewer_id: 'reviewer_3', confirmed_at: '2026-07-22T15:00:00Z', case_title: 'Mandaean Hate Speech'
  },
];

const Evidence = () => {
  const [packages, setPackages] = useState([]);
  const [selected, setSelected] = useState(null);
  const [searchTerm, setSearchTerm] = useState('');
  const [platformFilter, setPlatformFilter] = useState('all');

  useEffect(() => {
    setTimeout(() => setPackages(STUB_EVIDENCE), 500);
  }, []);

  const filtered = packages.filter(p => {
    const matchPlatform = platformFilter === 'all' || p.platform === platformFilter;
    const matchSearch = !searchTerm || 
      p.target_group?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.classification?.toLowerCase().includes(searchTerm.toLowerCase()) ||
      p.case_title?.toLowerCase().includes(searchTerm.toLowerCase());
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

      <div className="evidence-toolbar">
        <div className="search-bar">
          <Search size={18} className="search-icon" />
          <input
            type="text"
            placeholder="Search by target group, classification, or case..."
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
                <span className={`platform-badge ${pkg.platform} sm`}>{pkg.platform}</span>
                <StatusBadge status={pkg.severity} size="sm" />
              </div>
              <h4 className="ev-item-classification">{pkg.classification}</h4>
              <div className="ev-item-meta">
                <span className="ev-item-group">{pkg.target_group}</span>
                <span className="ev-item-case">{pkg.case_title}</span>
              </div>
              <div className="ev-item-footer">
                <Calendar size={12} />
                <span>{new Date(pkg.confirmed_at).toLocaleDateString()}</span>
                <span className="ev-item-reviewer">by {pkg.reviewer_id}</span>
              </div>
            </div>
          ))}
          {filtered.length === 0 && (
            <div className="evidence-empty">
              <Image size={40} />
              <p>No evidence packages match your filters.</p>
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
