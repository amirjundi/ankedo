import React, { useState, useEffect } from 'react';
import StatusBadge from '../components/StatusBadge';
import { Bell, CheckCircle, Clock, AlertTriangle, MessageSquare } from 'lucide-react';
import './Notifications.css';

const STUB_NOTIFICATIONS = [
  {
    id: 'n_001', type: 'account_blocked', urgency: 'critical',
    question: 'Facebook account fb_worker_3 has been blocked. Current capacity dropped to 4/6. What would you like to do?',
    suggested_actions: ['1. Add backup account', '2. Pause Facebook monitoring', '3. Wait and retry in 24h'],
    created_at: '2026-07-23T10:15:00Z', status: 'pending',
  },
  {
    id: 'n_002', type: 'case_reactivation', urgency: 'high',
    question: 'Watch keywords for "Shabak Community Targeting" case have resurfaced on 3 new posts. Recommend reactivation?',
    suggested_actions: ['1. Reactivate case', '2. Keep dormant', '3. Show me the posts first'],
    created_at: '2026-07-23T09:45:00Z', status: 'pending',
  },
  {
    id: 'n_003', type: 'discovery_report', urgency: 'medium',
    question: 'Discovered 2 new pages with high hate speech density during routine scanning. Add to watch list?',
    suggested_actions: ['1. Add both to watch list', '2. Show me details first', '3. Ignore'],
    created_at: '2026-07-23T08:30:00Z', status: 'pending',
  },
  {
    id: 'n_004', type: 'queue_overflow', urgency: 'high',
    question: 'Review queue has reached 47 items (threshold: 30). Batch borderline items or request additional reviewer?',
    suggested_actions: ['1. Batch borderline items', '2. Increase auto-flag threshold', '3. I will review now'],
    created_at: '2026-07-22T23:00:00Z', status: 'resolved', response: 'I will review now',
  },
];

const ICON_MAP = {
  account_blocked: AlertTriangle,
  case_reactivation: Clock,
  discovery_report: MessageSquare,
  queue_overflow: Bell,
};

const Notifications = () => {
  const [notifications, setNotifications] = useState([]);
  const [filter, setFilter] = useState('all');

  useEffect(() => {
    setTimeout(() => setNotifications(STUB_NOTIFICATIONS), 400);
  }, []);

  const filtered = notifications.filter(n => {
    if (filter === 'pending') return n.status === 'pending';
    if (filter === 'resolved') return n.status === 'resolved';
    return true;
  });

  const handleRespond = (notifId, action) => {
    setNotifications(prev => prev.map(n =>
      n.id === notifId ? { ...n, status: 'resolved', response: action } : n
    ));
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
                  <span className="notif-type">{notif.type.replace(/_/g, ' ')}</span>
                  <span className="notif-time">{new Date(notif.created_at).toLocaleString()}</span>
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
