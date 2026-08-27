import React, { useState, useEffect } from 'react';
import StatusBadge from '../components/StatusBadge';
import { Bell, CheckCircle, Clock, AlertTriangle, MessageSquare } from 'lucide-react';
import { api, ApiError } from '../api';
import './Notifications.css';

const ICON_MAP = {
  account_blocked: AlertTriangle,
  case_reactivation: Clock,
  discovery_report: MessageSquare,
  queue_overflow: Bell,
};

const Notifications = () => {
  const [notifications, setNotifications] = useState([]);
  const [filter, setFilter] = useState('all');
  const [error, setError] = useState(null);

  const load = async () => {
    try {
      const data = await api.notifications();
      setNotifications(data.notifications || []);
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  useEffect(() => {
    load();
    const timer = setInterval(load, 60_000);
    return () => clearInterval(timer);
  }, []);

  const filtered = notifications.filter(n => {
    if (filter === 'pending') return n.status === 'pending';
    if (filter === 'resolved') return n.status === 'resolved';
    return true;
  });

  // The old handler only changed local state: the operator answered the agent's
  // question, the card moved to Resolved, and the agent never heard the answer.
  const handleRespond = async (notifId, action) => {
    try {
      await api.respondToNotification(notifId, { action_taken: action });
      setNotifications(prev => prev.map(n =>
        n.id === notifId ? { ...n, status: 'resolved', response: action } : n
      ));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    }
  };

  return (
    <div className="notifications-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Notifications</h1>
          <p className="page-subtitle">Agent questions and alerts requiring your response</p>
        </div>
        <div className="notif-stats">
          <span className="notif-pending-count">
            {notifications.filter(n => n.status === 'pending').length} pending
          </span>
        </div>
      </header>

      {error && <div className="glass-panel" style={{ padding: '1rem', marginBottom: '1rem' }}>{error}</div>}

      <div className="notif-filters">
        {['all', 'pending', 'resolved'].map(f => (
          <button key={f} className={`filter-btn ${filter === f ? 'active' : ''}`} onClick={() => setFilter(f)}>
            {f === 'all' ? 'All' : f}
          </button>
        ))}
      </div>

      <div className="notifications-list">
        {filtered.map(notif => {
          const IconComponent = ICON_MAP[notif.type] || Bell;
          return (
            <div key={notif.id} className={`notif-card glass-panel ${notif.status}`}>
              <div className="notif-card-header">
                <div className={`notif-icon-wrap ${notif.urgency}`}>
                  <IconComponent size={20} />
                </div>
                <div className="notif-header-text">
                  <span className="notif-type">{(notif.type || '').replace(/_/g, ' ')}</span>
                  <span className="notif-time">{notif.created_at ? new Date(notif.created_at).toLocaleString() : ''}</span>
                </div>
                <StatusBadge status={notif.urgency} size="sm" />
              </div>

              <p className="notif-question">{notif.question}</p>

              {notif.status === 'pending' ? (
                <div className="notif-actions">
                  {notif.suggested_actions?.map((action, i) => (
                    <button key={i} className="notif-action-btn" onClick={() => handleRespond(notif.id, action)}>
                      {action}
                    </button>
                  ))}
                </div>
              ) : (
                <div className="notif-resolved-bar">
                  <CheckCircle size={16} />
                  <span>Resolved: {notif.response}</span>
                </div>
              )}
            </div>
          );
        })}

        {filtered.length === 0 && (
          <div className="notif-empty glass-panel">
            <Bell size={40} />
            <p>No notifications to show.</p>
          </div>
        )}
      </div>
    </div>
  );
};

export default Notifications;
