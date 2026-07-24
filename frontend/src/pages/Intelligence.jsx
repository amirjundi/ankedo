import React, { useState, useEffect } from 'react';
import { Search, AlertTriangle } from 'lucide-react';
import './Intelligence.css';

const Intelligence = () => {
  const [offenders, setOffenders] = useState([]);

  useEffect(() => {
    // Stub fetch
    setTimeout(() => {
      setOffenders([
        { id: 'acc_01', platform: 'facebook', handle: 'angry_user99', offenses: 14, lastActive: '2 mins ago', status: 'Active Surveillance' },
        { id: 'acc_02', platform: 'tiktok', handle: 'hate_speech_bot', offenses: 42, lastActive: '1 hour ago', status: 'Banned (Linked)' },
        { id: 'acc_03', platform: 'instagram', handle: 'troll_farm_alpha', offenses: 8, lastActive: 'Yesterday', status: 'Active Surveillance' },
      ]);
    }, 600);
  }, []);

  return (
    <div className="intelligence animate-fade-in">
      <header className="page-header flex justify-between items-center">
        <div>
          <h1>Intelligence Hub</h1>
          <p className="page-subtitle">Tracked actors and threat timelines</p>
        </div>
        <div className="search-bar">
          <Search size={18} className="search-icon" />
          <input type="text" placeholder="Search handles or IDs..." />
        </div>
      </header>

      <div className="intelligence-content glass-panel">
        <div className="table-header">
          <h3>Repeat Offenders Watchlist</h3>
          <span className="badge-warning"><AlertTriangle size={14}/> High Priority</span>
        </div>
        
        <table className="data-table">
          <thead>
            <tr>
              <th>Handle</th>
              <th>Platform</th>
              <th>Confirmed Offenses</th>
              <th>Last Active</th>
              <th>Status</th>
              <th>Action</th>
            </tr>
          </thead>
          <tbody>
            {offenders.map(actor => (
              <tr key={actor.id}>
                <td className="handle">@{actor.handle}</td>
                <td><span className={`platform-badge ${actor.platform} sm`}>{actor.platform}</span></td>
                <td className="offenses">{actor.offenses}</td>
                <td className="last-active">{actor.lastActive}</td>
                <td>
                  <span className={`status-pill ${actor.status.includes('Banned') ? 'banned' : 'active'}`}>
                    {actor.status}
                  </span>
                </td>
                <td>
                  <button className="btn-view">View Timeline</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

export default Intelligence;
