import React from 'react';
import './MetricCard.css';

const MetricCard = ({ title, value, subtext, icon: Icon, trend }) => {
  return (
    <div className="metric-card glass-panel animate-fade-in">
      <div className="metric-header flex justify-between items-center">
        <h3 className="metric-title">{title}</h3>
        {Icon && <div className="metric-icon"><Icon size={20} /></div>}
      </div>
      
      <div className="metric-content">
        <div className="metric-value">{value}</div>
        {(subtext || trend) && (
          <div className="metric-footer flex items-center gap-2">
            {trend && (
              <span className={`metric-trend ${trend > 0 ? 'positive' : 'negative'}`}>
                {trend > 0 ? '↑' : '↓'} {Math.abs(trend)}%
              </span>
            )}
            {subtext && <span className="metric-subtext">{subtext}</span>}
          </div>
        )}
      </div>
    </div>
  );
};

export default MetricCard;
