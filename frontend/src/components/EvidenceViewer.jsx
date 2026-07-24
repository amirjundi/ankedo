import React from 'react';
import { Image, ExternalLink, FileText, Shield } from 'lucide-react';
import StatusBadge from './StatusBadge';
import './EvidenceViewer.css';

const EvidenceViewer = ({ evidence, onClose }) => {
  if (!evidence) return null;

  return (
    <div className="evidence-viewer glass-panel animate-fade-in">
      <div className="ev-header">
        <h3>Evidence Package</h3>
        <span className="ev-id">#{evidence.id?.slice(0, 8)}</span>
      </div>

      <div className="ev-screenshot-area">
        {evidence.screenshot_path ? (
          <img src={evidence.screenshot_path} alt="Evidence screenshot" className="ev-screenshot" />
        ) : (
          <div className="ev-screenshot-placeholder">
            <Image size={40} />
            <span>Screenshot not available</span>
          </div>
        )}
      </div>

      <div className="ev-details">
        <div className="ev-detail-row">
          <ExternalLink size={14} />
          <span className="ev-label">Source URL</span>
          <a href={evidence.url || '#'} target="_blank" rel="noopener noreferrer" className="ev-value ev-link">
            {evidence.url || 'N/A'}
          </a>
        </div>

        <div className="ev-detail-row">
          <Shield size={14} />
          <span className="ev-label">Severity</span>
          <StatusBadge status={evidence.severity || 'medium'} size="sm" />
        </div>

        <div className="ev-detail-row">
          <FileText size={14} />
          <span className="ev-label">Classification</span>
          <span className="ev-value">{evidence.classification || 'Hate Speech'}</span>
        </div>

        {evidence.target_group && (
          <div className="ev-detail-row">
            <span className="ev-label" style={{marginLeft: '1.15rem'}}>Target Group</span>
            <span className="ev-value">{evidence.target_group}</span>
          </div>
        )}

        {evidence.trope_fired && (
          <div className="ev-trope-box">
            <span className="ev-trope-label">Trope Activated</span>
            <span className="ev-trope-value">{evidence.trope_fired}</span>
          </div>
        )}

        {evidence.reviewer_id && (
          <div className="ev-detail-row">
            <span className="ev-label" style={{marginLeft: '1.15rem'}}>Reviewed by</span>
            <span className="ev-value">{evidence.reviewer_id}</span>
          </div>
        )}

        {evidence.confirmed_at && (
          <div className="ev-detail-row">
            <span className="ev-label" style={{marginLeft: '1.15rem'}}>Confirmed</span>
            <span className="ev-value ev-time">{new Date(evidence.confirmed_at).toLocaleString()}</span>
          </div>
        )}
      </div>

      {onClose && (
        <button className="ev-close-btn" onClick={onClose}>Close</button>
      )}
    </div>
  );
};

export default EvidenceViewer;
