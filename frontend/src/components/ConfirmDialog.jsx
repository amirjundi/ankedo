import React from 'react';
import './ConfirmDialog.css';
import { AlertTriangle, X } from 'lucide-react';

const ConfirmDialog = ({ isOpen, title, message, onConfirm, onCancel, variant = 'warning' }) => {
  if (!isOpen) return null;

  return (
    <div className="confirm-overlay" onClick={onCancel}>
      <div className="confirm-dialog glass-panel animate-fade-in" onClick={e => e.stopPropagation()}>
        <div className={`confirm-icon-wrap ${variant}`}>
          <AlertTriangle size={24} />
        </div>
        <h3 className="confirm-title">{title || 'Confirm Action'}</h3>
        <p className="confirm-message">{message}</p>
        <div className="confirm-actions">
          <button className="btn-cancel" onClick={onCancel}>
            <X size={16} /> Cancel
          </button>
          <button className={`btn-proceed ${variant}`} onClick={onConfirm}>
            Confirm
          </button>
        </div>
      </div>
    </div>
  );
};

export default ConfirmDialog;
