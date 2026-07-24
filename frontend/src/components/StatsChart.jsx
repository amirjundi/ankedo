import React from 'react';
import './StatsChart.css';

/**
 * A pure-CSS bar chart for visualizing time-series or categorical data.
 * No external charting library needed.
 */
const StatsChart = ({ title, data = [], maxValue, labelKey = 'label', valueKey = 'value', colorFn }) => {
  const computedMax = maxValue || Math.max(...data.map(d => d[valueKey]), 1);
  
  const defaultColorFn = (value, index) => {
    const hue = 221 + (index * 30) % 120;
    return `hsl(${hue}, 70%, 55%)`;
  };
  
  const getColor = colorFn || defaultColorFn;

  return (
    <div className="stats-chart">
      {title && <h3 className="chart-title">{title}</h3>}
      <div className="chart-bars">
        {data.map((item, index) => {
          const pct = Math.max(2, (item[valueKey] / computedMax) * 100);
          const color = getColor(item[valueKey], index);
          return (
            <div key={index} className="chart-bar-group">
              <div className="chart-bar-track">
                <div
                  className="chart-bar-fill animate-bar"
                  style={{
                    width: `${pct}%`,
                    background: `linear-gradient(90deg, ${color}, ${color}dd)`,
                    animationDelay: `${index * 0.08}s`,
                  }}
                >
                  <span className="chart-bar-value">{item[valueKey]}</span>
                </div>
              </div>
              <span className="chart-bar-label">{item[labelKey]}</span>
            </div>
          );
        })}
      </div>
      {data.length === 0 && (
        <div className="chart-empty">No data available</div>
      )}
    </div>
  );
};

export default StatsChart;
