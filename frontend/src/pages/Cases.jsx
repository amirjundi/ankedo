import React, { useState, useEffect } from 'react';
import CaseCard from '../components/CaseCard';
import StatusBadge from '../components/StatusBadge';
import ConfirmDialog from '../components/ConfirmDialog';
import { Plus, Filter, FolderOpen } from 'lucide-react';
import './Cases.css';

const STUB_CASES = [
  {
    id: 'case_001', title: 'Anti-Yazidi Campaign July 2026', target_group: 'Yazidi',
    state: 'active', watch_keywords: ['عبدة الشيطان', 'ايزيدي', 'شنكال', 'لالش'],
    items_count: 342, flagged_count: 89, last_activity: '12 min ago', dialect_scope: 'Iraqi Arabic + Kurmanji'
  },
  {
    id: 'case_002', title: 'Christian Displacement Narrative', target_group: 'Christian',
    state: 'cooling', watch_keywords: ['مسيحي', 'نصراني', 'سهل نينوى'],
    items_count: 156, flagged_count: 34, last_activity: '3 hours ago', dialect_scope: 'MSA + Iraqi'
  },
  {
    id: 'case_003', title: 'Shabak Community Targeting', target_group: 'Shabak',
    state: 'dormant', watch_keywords: ['شبك', 'برطلة'],
    items_count: 78, flagged_count: 12, last_activity: '2 weeks ago', dialect_scope: 'Iraqi Arabic'
  },
  {
    id: 'case_004', title: 'Mandaean Hate Speech Spike', target_group: 'Mandaean',
    state: 'reactivated', watch_keywords: ['صابئة', 'مندائي'],
    items_count: 201, flagged_count: 67, last_activity: '5 min ago', dialect_scope: 'Iraqi Arabic'
  },
];

const Cases = () => {
  const [cases, setCases] = useState([]);
  const [filter, setFilter] = useState('all');
  const [showNewCase, setShowNewCase] = useState(false);
  const [selectedCase, setSelectedCase] = useState(null);

  useEffect(() => {
    // Stub fetch — would connect to /api/admin or a dedicated cases endpoint
    setTimeout(() => setCases(STUB_CASES), 400);
  }, []);

  const filtered = filter === 'all' ? cases : cases.filter(c => c.state === filter);

  const stateFilters = ['all', 'active', 'cooling', 'dormant', 'reactivated'];

  return (
    <div className="cases-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Cases</h1>
          <p className="page-subtitle">Incident lifecycle management — Active, Cooling, Dormant, Reactivated</p>
        </div>
        <button className="btn-new-case" onClick={() => setShowNewCase(true)}>
          <Plus size={18} /> New Case
        </button>
      </header>

      <div className="cases-filters">
        <Filter size={16} className="filter-icon" />
        {stateFilters.map(f => (
          <button
            key={f}
            className={`filter-btn ${filter === f ? 'active' : ''}`}
            onClick={() => setFilter(f)}
          >
            {f === 'all' ? 'All' : f}
            {f !== 'all' && (
              <span className="filter-count">{cases.filter(c => c.state === f).length}</span>
            )}
          </button>
        ))}
      </div>

      <div className="cases-grid">
        {filtered.map(c => (
          <CaseCard key={c.id} caseData={c} onClick={setSelectedCase} />
        ))}
        {filtered.length === 0 && (
          <div className="cases-empty glass-panel">
            <FolderOpen size={40} />
            <p>No cases match the selected filter.</p>
          </div>
        )}
      </div>

      {/* New Case Modal */}
      {showNewCase && (
        <div className="modal-overlay" onClick={() => setShowNewCase(false)}>
          <div className="modal-content glass-panel animate-fade-in" onClick={e => e.stopPropagation()}>
            <h2>Register New Case</h2>
            <form className="new-case-form" onSubmit={e => { e.preventDefault(); setShowNewCase(false); }}>
              <div className="form-group">
                <label>Case Title</label>
                <input type="text" placeholder="e.g., Anti-Yazidi Campaign July 2026" />
              </div>
              <div className="form-group">
                <label>Target Group</label>
                <select defaultValue="">
                  <option value="" disabled>Select target group...</option>
                  <option>Yazidi</option>
                  <option>Christian</option>
                  <option>Shabak</option>
                  <option>Mandaean</option>
                  <option>Turkmen</option>
                  <option>Faili Kurd</option>
                  <option>Other</option>
                </select>
              </div>
              <div className="form-group">
                <label>Watch Keywords (comma-separated)</label>
                <input type="text" placeholder="عبدة الشيطان, ايزيدي, شنكال" />
              </div>
              <div className="form-group">
                <label>Seed Posts / URLs (one per line)</label>
                <textarea rows={3} placeholder="https://facebook.com/..." />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Dialect Scope</label>
                  <select defaultValue="iraqi">
                    <option value="iraqi">Iraqi Arabic</option>
                    <option value="msa">MSA</option>
                    <option value="sorani">Sorani Kurdish</option>
                    <option value="kurmanji">Kurmanji Kurdish</option>
                    <option value="mixed">Mixed</option>
                  </select>
                </div>
                <div className="form-group">
                  <label>Initial State</label>
                  <select defaultValue="active">
                    <option value="active">Active</option>
                    <option value="cooling">Cooling</option>
                  </select>
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn-cancel-form" onClick={() => setShowNewCase(false)}>Cancel</button>
                <button type="submit" className="btn-submit-form">Create Case</button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* Case Detail Panel */}
      {selectedCase && (
        <div className="modal-overlay" onClick={() => setSelectedCase(null)}>
          <div className="modal-content glass-panel animate-fade-in case-detail" onClick={e => e.stopPropagation()}>
            <div className="case-detail-header">
              <h2>{selectedCase.title}</h2>
              <StatusBadge status={selectedCase.state} size="lg" pulse={selectedCase.state === 'active'} />
            </div>
            <div className="case-detail-grid">
              <div className="detail-item">
                <span className="detail-label">Target Group</span>
                <span className="detail-value">{selectedCase.target_group}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Dialect</span>
                <span className="detail-value">{selectedCase.dialect_scope}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Last Activity</span>
                <span className="detail-value">{selectedCase.last_activity}</span>
              </div>
              <div className="detail-item">
                <span className="detail-label">Items / Flagged</span>
                <span className="detail-value">{selectedCase.items_count} / <span style={{color: 'var(--accent-red)'}}>{selectedCase.flagged_count}</span></span>
              </div>
            </div>
            <div className="case-detail-keywords">
              <span className="detail-label">Watch Keywords</span>
              <div className="keywords-list">
                {selectedCase.watch_keywords.map((kw, i) => (
                  <span key={i} className="keyword-chip">{kw}</span>
                ))}
              </div>
            </div>
            <div className="case-detail-actions">
              <button className="btn-cancel-form" onClick={() => setSelectedCase(null)}>Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Cases;
