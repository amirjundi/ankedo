import React, { useState, useEffect } from 'react';
import CaseCard from '../components/CaseCard';
import StatusBadge from '../components/StatusBadge';
import ConfirmDialog from '../components/ConfirmDialog';
import { Plus, Filter, FolderOpen } from 'lucide-react';
import { api, ApiError } from '../api';
import './Cases.css';

const Cases = () => {
  const [cases, setCases] = useState([]);
  const [filter, setFilter] = useState('all');
  const [showNewCase, setShowNewCase] = useState(false);
  const [selectedCase, setSelectedCase] = useState(null);

  const [groups, setGroups] = useState([]);
  const [error, setError] = useState(null);
  const [form, setForm] = useState({ title: '', group: '', keywords: '', dialect: 'Iraqi Arabic' });
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const [c, g] = await Promise.all([api.cases(), api.targetGroups()]);
      setCases(c.cases || []);
      setGroups(g.target_groups || []);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => { load(); }, []);

  // Previously this closed the modal and threw the form away. An operator who
  // believes they opened a monitoring campaign that does not exist is worse off than
  // one who was never offered the button.
  const handleCreate = async (e) => {
    e.preventDefault();
    setSaving(true);
    try {
      await api.createCase({
        target_group_id: form.group,
        narrative_pattern: form.title,
        watch_keywords: form.keywords.split(',').map((k) => k.trim()).filter(Boolean),
        dialect_scope: form.dialect,
      });
      setShowNewCase(false);
      setForm({ title: '', group: '', keywords: '', dialect: 'Iraqi Arabic' });
      await load();
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSaving(false);
    }
  };

  const filtered = filter === 'all'
    ? cases
    : cases.filter(c => (c.state || '').toLowerCase() === filter);

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

      {error && <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{error}</div>}

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
              <span className="filter-count">{cases.filter(c => (c.state || '').toLowerCase() === f).length}</span>
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
            <form className="new-case-form" onSubmit={handleCreate}>
              <div className="form-group">
                <label>Case Title</label>
                <input type="text" required value={form.title}
                  onChange={e => setForm({ ...form, title: e.target.value })}
                  placeholder="e.g., Anti-Yazidi Campaign July 2026" />
              </div>
              <div className="form-group">
                <label>Target Group</label>
                <select required value={form.group}
                  onChange={e => setForm({ ...form, group: e.target.value })}>
                  <option value="" disabled>Select target group...</option>
                  {groups.map(g => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select>
              </div>
              <div className="form-group">
                <label>Watch Keywords (comma-separated)</label>
                <input type="text" value={form.keywords}
                  onChange={e => setForm({ ...form, keywords: e.target.value })}
                  placeholder="comma-separated" />
              </div>
              <div className="form-row">
                <div className="form-group">
                  <label>Dialect Scope</label>
                  <select value={form.dialect}
                    onChange={e => setForm({ ...form, dialect: e.target.value })}>
                    <option>Iraqi Arabic</option>
                    <option>MSA</option>
                    <option>Sorani Kurdish</option>
                    <option>Kurmanji Kurdish</option>
                    <option>Mixed</option>
                  </select>
                </div>
              </div>
              <div className="form-actions">
                <button type="button" className="btn-cancel-form" onClick={() => setShowNewCase(false)}>Cancel</button>
                <button type="submit" className="btn-submit-form" disabled={saving}>
                  {saving ? 'Creating…' : 'Create Case'}
                </button>
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
