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
  // Nothing else in the dashboard authenticates yet, so this page carries the only
  // way to supply the admin token. Shown when there is none, or one was rejected.
  const [needsToken, setNeedsToken] = useState(!token());
  const [tokenDraft, setTokenDraft] = useState('');

  const append = useCallback((msg) => {
    setMessages(prev => [...prev, { timestamp: new Date().toISOString(), ...msg }]);
  }, []);

  const post = useCallback(async (body) => {
    setIsLoading(true);
    try {
      // Only send the header when there is something to put in it. "Bearer " with an
      // empty token is an illegal header value, and the browser rejects the request
      // before it leaves — surfacing as "Failed to fetch", which reads like the agent
      // is unreachable when in fact nothing was ever sent.
      const bearer = token();
      const res = await fetch(CHAT_URL, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(bearer ? { Authorization: `Bearer ${bearer}` } : {}),
        },
        body: JSON.stringify(body),
      });

      if (res.status === 401 || res.status === 403) {
        setNeedsToken(true);
        append({
          direction: 'agent',
          content: bearer
            ? 'That token was rejected. Check ADMIN_API_TOKEN in your .env.'
            : 'I need your admin token before we can talk. Paste it below.',
        });
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

  const saveToken = useCallback(() => {
    const value = tokenDraft.trim();
    if (!value) return;
    // sessionStorage, not localStorage: the token dies with the tab rather than
    // sitting on disk on a machine that holds evidence about people at risk.
    sessionStorage.setItem('ankedo_token', value);
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
