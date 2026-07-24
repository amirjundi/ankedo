import React, { useState, useCallback } from 'react';
import ChatPanel from '../components/ChatPanel';
import './Chat.css';

const INITIAL_MESSAGES = [
  {
    direction: 'agent',
    content: 'مرحباً! أنا AnkEdo، مساعدك في مراقبة خطاب الكراهية. كيف يمكنني مساعدتك؟\n\nHello! I\'m AnkEdo, your hate speech monitoring assistant. How can I help you?',
    timestamp: new Date(Date.now() - 60000).toISOString(),
  },
];

const STUB_RESPONSES = {
  'queue': 'Currently there are **5 items** in the review queue:\n- 3 high-severity (Facebook)\n- 1 medium (TikTok)\n- 1 low (Instagram)\n\nThe oldest item has been waiting for 23 minutes.',
  'cases': 'There are **4 active cases**:\n1. Anti-Yazidi Campaign (342 items, 89 flagged)\n2. Christian Displacement (156 items, 34 flagged)\n3. Shabak Community (78 items, 12 flagged)\n4. Mandaean Hate Speech (201 items, 67 flagged)',
  'status': 'System status: ✅ Healthy\n- Crawl throughput: 42.5/s\n- Queue depth: 5 review, 45 processing\n- Active crawlers: 8 across 3 platforms\n- Classifier latency: 1.25s',
  default: 'I understood your message. Let me look into that for you.\n\n*Note: In a full implementation, this would process your query against the database and available MCP tools.*',
};

const Chat = () => {
  const [messages, setMessages] = useState(INITIAL_MESSAGES);
  const [isLoading, setIsLoading] = useState(false);

  const handleSend = useCallback((text) => {
    const adminMsg = {
      direction: 'admin',
      content: text,
      timestamp: new Date().toISOString(),
    };
    setMessages(prev => [...prev, adminMsg]);
    setIsLoading(true);

    // Simulate agent response
    setTimeout(() => {
      const lower = text.toLowerCase();
      let response = STUB_RESPONSES.default;
      let toolUsed = null;

      if (lower.includes('queue') || lower.includes('review') || lower.includes('طابور')) {
        response = STUB_RESPONSES.queue;
      } else if (lower.includes('case') || lower.includes('حالة') || lower.includes('قضية')) {
        response = STUB_RESPONSES.cases;
      } else if (lower.includes('status') || lower.includes('health') || lower.includes('حالة النظام')) {
        response = STUB_RESPONSES.status;
        toolUsed = 'system_health_check';
      }

      const agentMsg = {
        direction: 'agent',
        content: response,
        timestamp: new Date().toISOString(),
        tool_used: toolUsed,
      };
      setMessages(prev => [...prev, agentMsg]);
      setIsLoading(false);
    }, 1200 + Math.random() * 800);
  }, []);

  return (
    <div className="chat-page animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Agent Chat</h1>
          <p className="page-subtitle">Natural language interface — ask questions, give instructions, view alerts</p>
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
    </div>
  );
};

export default Chat;
