import React from 'react';
import './StatusBadge.css';

const STATUS_CONFIG = {
  active: { label: 'Active', className: 'status-active' },
  cooling: { label: 'Cooling', className: 'status-cooling' },
  dormant: { label: 'Dormant', className: 'status-dormant' },
  reactivated: { label: 'Reactivated', className: 'status-reactivated' },
  banned: { label: 'Banned', className: 'status-banned' },
  healthy: { label: 'Healthy', className: 'status-healthy' },
  quarantine: { label: 'Quarantine', className: 'status-quarantine' },
  pending: { label: 'Pending', className: 'status-pending' },
  resolved: { label: 'Resolved', className: 'status-resolved' },
  critical: { label: 'Critical', className: 'status-critical' },
  high: { label: 'High', className: 'status-high' },
  medium: { label: 'Medium', className: 'status-medium' },
  low: { label: 'Low', className: 'status-low' },
};

const StatusBadge = ({ status, size = 'md', pulse = false }) => {
  const config = STATUS_CONFIG[status?.toLowerCase()] || { label: status, className: 'status-default' };
  
  return (
    <span className={`status-badge ${config.className} size-${size} ${pulse ? 'pulse' : ''}`}>
      {pulse && <span className="status-dot" />}
      {config.label}
    </span>
  );
};

export default StatusBadge;
