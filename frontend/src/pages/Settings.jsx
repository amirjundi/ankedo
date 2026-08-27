import React, { useState, useEffect } from 'react';
import { Sliders, Shield, Database, AlertCircle } from 'lucide-react';
import ConfirmDialog from '../components/ConfirmDialog';
import { api, ApiError } from '../api';
import './Settings.css';

// What this page used to be: a Telegram token field, a WhatsApp token field, a matrix
// of alert-routing switches, per-platform account counts, and a Trigger Backup button.
// None of them were connected. The confirm dialog said "This will update the live
// system configuration" and its onConfirm closed the dialog. An operator could set a
// threshold, see it confirmed, and have changed nothing.
//
// Three tabs remain, and each one does what it says.
//
// The credential fields are gone rather than wired. API keys, the admin token and the
// Ettok agent key are absent from the server's allowlist by construction, so they
// cannot be read or written through the API at all — that is a deliberate boundary,
// not a gap. A form that cannot save is a worse answer than an instruction that works,
// so the page says where to set them instead.

const TABS = [
  { id: 'agent', label: 'Agent', icon: Sliders },
  { id: 'accounts', label: 'Platform Accounts', icon: Shield },
  { id: 'system', label: 'System', icon: Database },
];

const Settings = () => {
  const [activeTab, setActiveTab] = useState('agent');
  const [settings, setSettings] = useState([]);
  const [drafts, setDrafts] = useState({});
  const [accounts, setAccounts] = useState([]);
  const [status, setStatus] = useState(null);
  const [error, setError] = useState(null);
  const [backupPath, setBackupPath] = useState('');
  const [confirmBackup, setConfirmBackup] = useState(false);

  const load = async () => {
    try {
      const [c, a] = await Promise.all([api.config(), api.accounts()]);
      setSettings(c.settings || []);
      setDrafts(Object.fromEntries((c.settings || []).map((s) => [s.key, s.value])));
      setAccounts(a.accounts || []);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => {
    load();
  }, []);

  const save = async (key) => {
    setStatus(null);
    try {
      const res = await api.setConfig(key, drafts[key]);
      setStatus(res.message);
      setError(null);
      await load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const runBackup = async () => {
    setConfirmBackup(false);
    setStatus(null);
    try {
      const res = await api.backup(backupPath);
      setStatus(res.message || `Backup written to ${backupPath}`);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  const changed = (s) => drafts[s.key] !== s.value;

  return (
    <div className="settings-page animate-fade-in">
      <header className="page-header">
        <h1>Settings</h1>
        <p className="page-subtitle">Agent tuning, tracked accounts, and system management</p>
      </header>

      {error && (
        <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{error}</div>
      )}
      {status && (
        <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{status}</div>
      )}

      <div className="settings-layout">
        <nav className="settings-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.id}
              className={`settings-tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon size={18} /> {tab.label}
            </button>
          ))}
        </nav>

        <div className="settings-content">
          {activeTab === 'agent' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>Agent Settings</h2>
              <p className="panel-desc">
                Written to <code>.env</code> and validated before saving. Most take effect
                on the next cycle; model changes need a restart.
              </p>

              {settings.map((s) => (
                <div className="form-group" key={s.key}>
                  <label>
                    {s.key}
                    <span style={{ opacity: 0.6, fontWeight: 'normal' }}> — {s.description}</span>
                  </label>
                  <div style={{ display: 'flex', gap: '0.5rem' }}>
                    <input
                      type="text"
                      value={drafts[s.key] ?? ''}
                      onChange={(e) => setDrafts({ ...drafts, [s.key]: e.target.value })}
                    />
                    <button
                      className="btn-submit-form"
                      disabled={!changed(s)}
                      onClick={() => save(s.key)}
                    >
                      Save
                    </button>
                  </div>
                </div>
              ))}

              <div className="panel-note" style={{ marginTop: '1.5rem', opacity: 0.75 }}>
                <AlertCircle size={16} />{' '}
                API keys, the admin token and the platform key cannot be changed from the
                dashboard. Set them on the machine with{' '}
                <code>ankedo configure set KEY=value</code>.
              </div>
            </div>
          )}

          {activeTab === 'accounts' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>Platform Accounts</h2>
              <p className="panel-desc">Accounts the agent is tracking.</p>

              {accounts.length === 0 ? (
                <p style={{ opacity: 0.7 }}>No accounts are being tracked yet.</p>
              ) : (
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Handle</th>
                      <th>Platform</th>
                      <th>Status</th>
                    </tr>
                  </thead>
                  <tbody>
                    {accounts.map((a) => (
                      <tr key={a.id}>
                        <td>{a.handle}</td>
                        <td>
                          <span className={`platform-badge ${a.platform} sm`}>{a.platform}</span>
                        </td>
                        <td>{a.status}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {activeTab === 'system' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>System Management</h2>
              <p className="panel-desc">
                Export the database and agent state. The path is on the machine running
                the agent, not on yours.
              </p>

              <div className="form-group">
                <label>Destination path</label>
                <input
                  type="text"
                  value={backupPath}
                  onChange={(e) => setBackupPath(e.target.value)}
                  placeholder="/home/operator/ankedo-backup"
                />
              </div>
              <button
                className="btn-submit-form"
                disabled={!backupPath.trim()}
                onClick={() => setConfirmBackup(true)}
              >
                Trigger Backup
              </button>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        isOpen={confirmBackup}
        title="Run backup"
        message={`Copy the database and agent state to ${backupPath} on the agent's machine?`}
        onConfirm={runBackup}
        onCancel={() => setConfirmBackup(false)}
      />
    </div>
  );
};

export default Settings;
