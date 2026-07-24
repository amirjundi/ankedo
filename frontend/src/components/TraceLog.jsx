import React from 'react';
import './TraceLog.css';

const TraceLog = ({ traceData }) => {
  if (!traceData) return <div className="trace-empty">No trace data available.</div>;
  
  let parsedTrace = traceData;
  if (typeof traceData === 'string') {
    try {
      parsedTrace = JSON.parse(traceData);
    } catch (e) {
      return <div className="trace-error">Invalid trace format.</div>;
    }
  }

  return (
    <div className="trace-log-container glass-panel">
      <div className="trace-header">
        <h3>AI Committee Trace</h3>
        <span className="pulse-indicator">Live Analysis</span>
      </div>
      
      <div className="trace-body">
        {parsedTrace.map((entry, index) => (
          <div key={index} className={`trace-entry animate-fade-in`} style={{animationDelay: `${index * 0.1}s`}}>
            <div className="trace-agent">
              <span className="agent-badge">{entry.agent || 'Agent'}</span>
              <span className="trace-time">{entry.timestamp || new Date().toLocaleTimeString()}</span>
            </div>
            <div className="trace-content">
              {entry.decision && (
                <div className={`decision-pill ${entry.decision.toLowerCase()}`}>
                  {entry.decision}
                </div>
              )}
              <p className="trace-reason">{entry.reasoning || entry.message}</p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TraceLog;
