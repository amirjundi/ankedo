import React from 'react';
import { Bell } from 'lucide-react';
import './NotificationBell.css';

const NotificationBell = ({ count = 0, onClick }) => {
  return (
    <button className="notification-bell" onClick={onClick} title="Notifications">
      <Bell size={20} />
      {count > 0 && (
        <span className="notification-count">{count > 99 ? '99+' : count}</span>
      )}
    </button>
  );
};

export default NotificationBell;
