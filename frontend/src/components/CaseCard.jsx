import React from 'react';
import StatusBadge from './StatusBadge';
import { FolderOpen, MapPin, Clock, MessageSquare, Hash } from 'lucide-react';
import './CaseCard.css';

const CaseCard = ({ caseData, onClick }) => {
  const {
    id, title, target_group, state, watch_keywords = [],
    items_count = 0, flagged_count = 0, last_activity, dialect_scope
  } = caseData;

  return (
    <div className="case-card glass-panel animate-fade-in" onClick={() => onClick?.(caseData)}>
      <div className="case-card-header">
        <div className="case-card-title-row">
          <FolderOpen size={18} className="case-icon" />
          <h3 className="case-title">{title || `Case ${id?.slice(0, 8)}`}</h3>
        </div>
        <StatusBadge status={state} pulse={state === 'active' || state === 'reactivated'} />
      </div>

      <div className="case-card-meta">
        <div className="case-meta-item">
          <MapPin size={14} />
          <span>{target_group || 'Unspecified'}</span>
        </div>
        {dialect_scope && (
          <div className="case-meta-item">
            <MessageSquare size={14} />
            <span>{dialect_scope}</span>
          </div>
        )}
        {last_activity && (
          <div className="case-meta-item">
            <Clock size={14} />
            <span>{last_activity}</span>
          </div>
        )}
      </div>

      {watch_keywords.length > 0 && (
        <div className="case-keywords">
          {watch_keywords.slice(0, 4).map((kw, i) => (
            <span key={i} className="keyword-chip">
              <Hash size={10} />{kw}
            </span>
          ))}
          {watch_keywords.length > 4 && (
            <span className="keyword-chip keyword-more">+{watch_keywords.length - 4}</span>
          )}
        </div>
      )}

      <div className="case-card-footer">
        <div className="case-stat">
          <span className="case-stat-value">{items_count}</span>
          <span className="case-stat-label">Items</span>
        </div>
        <div className="case-stat-divider" />
        <div className="case-stat">
          <span className="case-stat-value flagged">{flagged_count}</span>
          <span className="case-stat-label">Flagged</span>
        </div>
        <div className="case-stat-divider" />
        <div className="case-stat">
          <span className="case-stat-value">{items_count > 0 ? ((flagged_count / items_count) * 100).toFixed(0) : 0}%</span>
          <span className="case-stat-label">Rate</span>
        </div>
      </div>
    </div>
  );
};

export default CaseCard;
