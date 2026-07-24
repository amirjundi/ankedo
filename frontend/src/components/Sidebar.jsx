import React, { useState, useEffect } from 'react';
import { NavLink, useNavigate } from 'react-router-dom';
import { LayoutDashboard, CheckSquare, Users, Settings, FolderOpen, Image, FileText, MessageCircle, Bell } from 'lucide-react';
import NotificationBell from './NotificationBell';
import './Sidebar.css';

const Sidebar = () => {
  const [notifCount, setNotifCount] = useState(0);
  const navigate = useNavigate();

  useEffect(() => {
    // Stub: simulate pending notification count
    setTimeout(() => setNotifCount(3), 1000);
  }, []);

  return (
    <aside className="sidebar glass-panel flex-col">
      <div className="sidebar-header">
        <div className="logo">
          <span className="logo-icon">▲</span>
          <h2>AnkEdo</h2>
        </div>
        <p className="subtitle">Hate Speech Monitor</p>
      </div>

      <nav className="sidebar-nav flex-col gap-2">
        <NavLink to="/" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`} end>
          <LayoutDashboard size={20} />
          <span>Dashboard</span>
        </NavLink>
        <NavLink to="/review" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <CheckSquare size={20} />
          <span>Review Queue</span>
        </NavLink>
        <NavLink to="/cases" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <FolderOpen size={20} />
          <span>Cases</span>
        </NavLink>
        <NavLink to="/intelligence" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <Users size={20} />
          <span>Intelligence</span>
        </NavLink>
        <NavLink to="/evidence" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <Image size={20} />
          <span>Evidence</span>
        </NavLink>
        <NavLink to="/reports" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <FileText size={20} />
          <span>Reports</span>
        </NavLink>

        <div className="nav-divider" />

        <NavLink to="/chat" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <MessageCircle size={20} />
          <span>Agent Chat</span>
        </NavLink>
        <NavLink to="/notifications" className={({isActive}) => `nav-item nav-notif-item ${isActive ? 'active' : ''}`}>
          <Bell size={20} />
          <span>Notifications</span>
          {notifCount > 0 && <span className="nav-notif-badge">{notifCount}</span>}
        </NavLink>
      </nav>

      <div className="sidebar-footer">
        <NavLink to="/settings" className={({isActive}) => `nav-item ${isActive ? 'active' : ''}`}>
          <Settings size={20} />
          <span>Settings</span>
        </NavLink>
      </div>
    </aside>
  );
};

export default Sidebar;
