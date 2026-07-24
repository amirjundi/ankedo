import React, { useState, useRef, useEffect } from 'react';
import { Send, Bot, User, Wrench, Loader } from 'lucide-react';
import './ChatPanel.css';

const ChatPanel = ({ messages = [], onSendMessage, isLoading = false }) => {
  const [input, setInput] = useState('');
  const messagesEndRef = useRef(null);
  const inputRef = useRef(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const handleSend = () => {
    const text = input.trim();
    if (!text || isLoading) return;
    onSendMessage?.(text);
    setInput('');
    inputRef.current?.focus();
  };

  const handleKeyDown = (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  // Simple RTL detection for Arabic text
  const isRTL = (text) => /[\u0600-\u06FF\u0750-\u077F]/.test(text);

  return (
    <div className="chat-panel">
      <div className="chat-messages">
        {messages.length === 0 && (
          <div className="chat-empty">
            <Bot size={40} />
            <h3>AnkEdo Agent</h3>
            <p>Ask me about the system status, cases, or give me instructions.</p>
          </div>
        )}
        {messages.map((msg, i) => (
          <div
            key={i}
            className={`chat-message ${msg.direction === 'agent' ? 'agent' : 'admin'} animate-fade-in`}
            style={{ animationDelay: `${i * 0.05}s` }}
          >
            <div className="chat-avatar">
              {msg.direction === 'agent' ? <Bot size={18} /> : <User size={18} />}
            </div>
            <div className="chat-bubble">
              <p className="chat-text" dir={isRTL(msg.content) ? 'rtl' : 'ltr'}>
                {msg.content}
              </p>
              {msg.tool_used && (
                <div className="chat-tool-indicator">
                  <Wrench size={12} />
                  <span>Used: {msg.tool_used}</span>
                </div>
              )}
              <span className="chat-time">
                {msg.timestamp ? new Date(msg.timestamp).toLocaleTimeString() : new Date().toLocaleTimeString()}
              </span>
            </div>
          </div>
        ))}
        {isLoading && (
          <div className="chat-message agent">
            <div className="chat-avatar"><Bot size={18} /></div>
            <div className="chat-bubble chat-typing">
              <Loader size={16} className="spin" />
              <span>Thinking...</span>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      <div className="chat-input-area">
        <textarea
          ref={inputRef}
          className="chat-input"
          placeholder="Type a message... (Arabic and English supported)"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={handleKeyDown}
          rows={1}
          dir={isRTL(input) ? 'rtl' : 'ltr'}
        />
        <button
          className={`chat-send-btn ${input.trim() ? 'active' : ''}`}
          onClick={handleSend}
          disabled={!input.trim() || isLoading}
        >
          <Send size={18} />
        </button>
      </div>
    </div>
  );
};

export default ChatPanel;
