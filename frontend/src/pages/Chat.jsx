import React, { useState, useCallback } from 'react';
import ChatPanel from '../components/ChatPanel';
import { api, ApiError, setToken } from '../api';
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


const Chat = () => {
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isLoading, setIsLoading] = useState(false);
  // A change the agent proposed and a human has not yet agreed to.
  const [pending, setPending] = useState(null);
  // Nothing else in the dashboard authenticates yet, so this page carries the only
  // way to supply the admin token. Shown when there is none, or one was rejected.
  // Not "is a token stored", but "has the agent actually refused". An agent running
  // on the operator's own machine no longer requires one, so opening with a password
  // prompt asked them for a credential nothing was going to check — and there was no
  // obvious way past it to find that out.
  const [needsToken, setNeedsToken] = useState(false);
  const [tokenDraft, setTokenDraft] = useState('');

  const append = useCallback((msg) => {
    setMessages(prev => [...prev, { timestamp: new Date().toISOString(), ...msg }]);
  }, []);

  const post = useCallback(async (body) => {
    setIsLoading(true);
    try {
      // api.js owns the token rule — the header is omitted entirely when there is no
      // token, because "Bearer " with an empty value is an illegal header that the
      // browser rejects before sending, surfacing as "Failed to fetch" and reading
      // exactly like the agent being down.
      const data = await api.chat(body);
      append({
        direction: 'agent',
        content: data.reply,
        tool_used: data.action_run || null,
      });
      // A mutating action comes back described but not performed.
      setPending(data.pending || null);
    } catch (err) {
      if (err instanceof ApiError && (err.status === 401 || err.status === 403)) {
        setNeedsToken(true);
      }
      append({
        direction: 'agent',
        content: err instanceof ApiError ? err.message : `Could not reach the agent: ${err.message}`,
      });
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

  const saveToken = useCallback(() => {
    const value = tokenDraft.trim();
    if (!value) return;
    setToken(value);
    setTokenDraft('');
    setNeedsToken(false);
    append({ direction: 'agent', content: 'Token saved. Ask me something.' });
  }, [tokenDraft, append]);

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

      {needsToken && (
        <div className="chat-token glass-panel">
          <label htmlFor="token">
            Admin token — the value of <code>ADMIN_API_TOKEN</code> in your .env
          </label>
          <div className="chat-token-row">
            <input
              id="token"
              type="password"
              value={tokenDraft}
              placeholder="paste it here"
              onChange={e => setTokenDraft(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && saveToken()}
            />
            <button onClick={saveToken} disabled={!tokenDraft.trim()}>Save</button>
          </div>
          <p className="chat-token-hint">
            Kept for this browser tab only. Find it with:
            <code>grep ADMIN_API_TOKEN ~/AnkEdo/.env</code>
          </p>
        </div>
      )}

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
