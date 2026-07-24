import React, { useState } from 'react';
import StatusBadge from '../components/StatusBadge';
import ConfirmDialog from '../components/ConfirmDialog';
import { Settings as SettingsIcon, Send, Shield, Bell, Database, Save, TestTube } from 'lucide-react';
import './Settings.css';

const Settings = () => {
  const [activeTab, setActiveTab] = useState('channels');
  const [showConfirm, setShowConfirm] = useState(false);
  const [testResult, setTestResult] = useState(null);

  // Channel config state
  const [telegram, setTelegram] = useState({ token: '', chatId: '', enabled: true });
  const [whatsapp, setWhatsapp] = useState({ phoneNumber: '', apiKey: '', enabled: false });

  // Notification prefs
  const [notifPrefs, setNotifPrefs] = useState({
    critical: { telegram: true, whatsapp: true, web: true },
    high: { telegram: true, whatsapp: false, web: true },
    medium: { telegram: false, whatsapp: false, web: true },
    low: { telegram: false, whatsapp: false, web: true },
  });

  const handleTestMessage = (channel) => {
    setTestResult({ channel, status: 'sending' });
    setTimeout(() => {
      setTestResult({ channel, status: 'success', message: `Test message sent to ${channel} successfully! 🤖` });
    }, 1500);
  };

  const toggleNotifPref = (level, channel) => {
    setNotifPrefs(prev => ({
      ...prev,
      [level]: { ...prev[level], [channel]: !prev[level][channel] }
    }));
  };

  const tabs = [
    { id: 'channels', label: 'Channels', icon: Send },
    { id: 'notifications', label: 'Alert Routing', icon: Bell },
    { id: 'accounts', label: 'Platform Accounts', icon: Shield },
    { id: 'system', label: 'System', icon: Database },
  ];

  return (
    <div className="settings-page animate-fade-in">
      <header className="page-header">
        <h1>Settings</h1>
        <p className="page-subtitle">Channel configuration, notification routing, and system management</p>
      </header>

      <div className="settings-layout">
        <nav className="settings-tabs">
          {tabs.map(tab => (
            <button
              key={tab.id}
              className={`settings-tab ${activeTab === tab.id ? 'active' : ''}`}
              onClick={() => setActiveTab(tab.id)}
            >
              <tab.icon size={18} />
              <span>{tab.label}</span>
            </button>
          ))}
        </nav>

        <div className="settings-content">
          {/* Channels Tab */}
          {activeTab === 'channels' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>Communication Channels</h2>
              <p className="panel-desc">Configure how the agent communicates with you.</p>

              <div className="channel-block">
                <div className="channel-header">
                  <h3>Telegram</h3>
                  <label className="toggle-switch">
                    <input type="checkbox" checked={telegram.enabled} onChange={e => setTelegram(p => ({...p, enabled: e.target.checked}))} />
                    <span className="toggle-slider" />
                  </label>
                </div>
                <div className="form-group">
                  <label>Bot Token</label>
                  <input type="password" placeholder="123456:ABC-DEF..." value={telegram.token}
                    onChange={e => setTelegram(p => ({...p, token: e.target.value}))} />
                </div>
                <div className="form-group">
                  <label>Admin Chat ID</label>
                  <input type="text" placeholder="123456789" value={telegram.chatId}
                    onChange={e => setTelegram(p => ({...p, chatId: e.target.value}))} />
                </div>
                <button className="btn-test" onClick={() => handleTestMessage('Telegram')}>
                  <TestTube size={16} /> Send Test Message
                </button>
                {testResult?.channel === 'Telegram' && (
                  <div className={`test-result ${testResult.status}`}>
                    {testResult.status === 'sending' ? 'Sending...' : testResult.message}
                  </div>
                )}
              </div>

              <div className="channel-block">
                <div className="channel-header">
                  <h3>WhatsApp</h3>
                  <label className="toggle-switch">
                    <input type="checkbox" checked={whatsapp.enabled} onChange={e => setWhatsapp(p => ({...p, enabled: e.target.checked}))} />
                    <span className="toggle-slider" />
                  </label>
                </div>
                <div className="form-group">
                  <label>Phone Number</label>
                  <input type="text" placeholder="+964XXXXXXXXX" value={whatsapp.phoneNumber}
                    onChange={e => setWhatsapp(p => ({...p, phoneNumber: e.target.value}))} />
                </div>
                <div className="form-group">
                  <label>API Key (WhatsApp Business Cloud API)</label>
                  <input type="password" placeholder="EAAG..." value={whatsapp.apiKey}
                    onChange={e => setWhatsapp(p => ({...p, apiKey: e.target.value}))} />
                </div>
                <button className="btn-test" onClick={() => handleTestMessage('WhatsApp')}>
                  <TestTube size={16} /> Send Test Message
                </button>
                {testResult?.channel === 'WhatsApp' && (
                  <div className={`test-result ${testResult.status}`}>
                    {testResult.status === 'sending' ? 'Sending...' : testResult.message}
                  </div>
                )}
              </div>

              <div className="settings-save-bar">
                <button className="btn-submit-form" onClick={() => setShowConfirm(true)}>
                  <Save size={16} /> Save Channel Settings
                </button>
              </div>
            </div>
          )}

          {/* Alert Routing Tab */}
          {activeTab === 'notifications' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>Alert Routing</h2>
              <p className="panel-desc">Choose which notification levels go to which channels.</p>

              <table className="notif-routing-table">
                <thead>
                  <tr>
                    <th>Alert Level</th>
                    <th>Telegram</th>
                    <th>WhatsApp</th>
                    <th>Web</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(notifPrefs).map(([level, channels]) => (
                    <tr key={level}>
                      <td><StatusBadge status={level} /></td>
                      {['telegram', 'whatsapp', 'web'].map(ch => (
                        <td key={ch}>
                          <label className="toggle-switch sm">
                            <input type="checkbox" checked={channels[ch]} onChange={() => toggleNotifPref(level, ch)} />
                            <span className="toggle-slider" />
                          </label>
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>

              <div className="settings-save-bar">
                <button className="btn-submit-form"><Save size={16} /> Save Preferences</button>
              </div>
            </div>
          )}

          {/* Platform Accounts Tab */}
          {activeTab === 'accounts' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>Platform Accounts</h2>
              <p className="panel-desc">Worker accounts used for social media collection.</p>

              <div className="accounts-grid">
                {[
                  { platform: 'facebook', active: 5, quarantine: 1 },
                  { platform: 'tiktok', active: 3, quarantine: 0 },
                  { platform: 'instagram', active: 4, quarantine: 2 },
                ].map(p => (
                  <div key={p.platform} className="account-platform-card">
                    <span className={`platform-badge ${p.platform}`}>{p.platform}</span>
                    <div className="account-stats">
                      <div className="account-stat">
                        <span className="account-stat-val active">{p.active}</span>
                        <span className="account-stat-label">Active</span>
                      </div>
                      <div className="account-stat">
                        <span className="account-stat-val quarantine">{p.quarantine}</span>
                        <span className="account-stat-label">Quarantine</span>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* System Tab */}
          {activeTab === 'system' && (
            <div className="settings-panel glass-panel animate-fade-in">
              <h2>System Management</h2>
              <p className="panel-desc">Backup, maintenance, and system controls.</p>

              <div className="system-actions">
                <div className="system-action-card">
                  <Database size={24} />
                  <div>
                    <h4>Database Backup</h4>
                    <p>Export the full SQLite database and agent state to a local path.</p>
                  </div>
                  <button className="btn-submit-form" onClick={() => setShowConfirm(true)}>Trigger Backup</button>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      <ConfirmDialog
        isOpen={showConfirm}
        title="Confirm Changes"
        message="Are you sure you want to save these changes? This will update the live system configuration."
        onConfirm={() => setShowConfirm(false)}
        onCancel={() => setShowConfirm(false)}
      />
    </div>
  );
};

export default Settings;
