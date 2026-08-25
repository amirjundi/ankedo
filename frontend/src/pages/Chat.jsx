import React, { useState, useCallback } from 'react';
import ChatPanel from '../components/ChatPanel';
import ConfirmDialog from '../components/ConfirmDialog';
import './Chat.css';

const INITIAL_MESSAGES = [
  {
    direction: 'agent',
    content: 'مرحباً! أنا AnkEdo، مساعدك في مراقبة خطاب الكراهية. كيف يمكنني مساعدتك؟\n\nHello! I\'m AnkEdo, your hate speech monitoring assistant. Ask about status, cases or flagged items — or tell me to change a setting.',
    timestamp: new Date(Date.now() - 60000).toISOString(),
  },
];

// The dashboard is served from the same origin as the API, so a relative path
// avoids a second host in CORS and in the tunnel config.
const CHAT_URL = '/api/chat';

// The bearer the rest of the dashboard already uses. Kept in sessionStorage so it
// dies with the tab rather than sitting in localStorage on a shared machine.
const token = () => sessionStorage.getItem('ankedo_token') || '';

const Chat = () => {
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isLoading, setIsLoading] = useState(false);
  // A change the agent proposed and a human has not yet agreed to.
  const [pending, setPending] = useState(null);

  const append = useCallback((msg) => {
    setMessages(prev => [...prev, { timestamp: new Date().toISOString(), ...msg }]);
  }, []);

  const post = useCallback(async (body) => {
    setIsLoading(true);
    try {
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token()}`,
        },
        body: JSON.stringify(body),
      });

      if (res.status === 401 || res.status === 403) {
        append({ direction: 'agent', content: 'Not authorised. Sign in again.' });
        return;
      }
      if (res.status === 503) {
        // Auth fails closed when ADMIN_API_TOKEN is unset — say which, since the
        // fix is on the machine and not in the browser.
        append({
          direction: 'agent',
          content: 'The API has no ADMIN_API_TOKEN configured. Run `ankedo setup` on the agent machine.',
        });
        return;
      }
      if (!res.ok) {
        append({ direction: 'agent', content: `Request failed (${res.status}).` });
        return;
      }

      const data = await res.json();
      append({
        direction: 'agent',
        content: data.reply,
        tool_used: data.action_run || null,
      });
      // A mutating action comes back described but not performed.
      setPending(data.pending || null);
    } catch (err) {
      append({ direction: 'agent', content: `Could not reach the agent: ${err.message}` });
    } finally {
      setIsLoading(false);
    }
  }, [append]);

  const handleSend = useCallback((text) => {
    append({ direction: 'admin', content: text });
    post({ message: text });
  }, [append, post]);

  const handleConfirm = useCallback(() => {
    const confirmed = pending;
    setPending(null);
    append({ direction: 'admin', content: 'Confirmed.' });
    post({ confirm: confirmed });
  }, [pending, append, post]);

  const handleCancel = useCallback(() => {
    setPending(null);
    append({ direction: 'agent', content: 'Cancelled — nothing was changed.' });
  }, [append]);

  const describe = (p) => {
    if (!p) return '';
    const a = p.arguments || {};
    if (p.action === 'set_config') {
      return `Change ${a.key} to "${a.value}"?`;
    }
    return `Run ${p.action}?`;
  };

  return (
    <div className="chat-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Agent Chat</h1>
          <p className="page-subtitle">Ask about the system, or change a setting — changes need your confirmation</p>
        </div>
        <div className="chat-status">
          <span className="chat-status-dot" />
          <span>Agent Online</span>
        </div>
      </header>

      <div className="chat-container glass-panel">
        <ChatPanel
          messages={messages}
          onSendMessage={handleSend}
          isLoading={isLoading}
        />
      </div>

      {pending && (
        <ConfirmDialog
          isOpen
          title="Confirm change"
          message={describe(pending)}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}
    </div>
  );
};

export default Chat;
