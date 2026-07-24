import React, { useState, useEffect } from 'react';
import TraceLog from '../components/TraceLog';
import StatusBadge from '../components/StatusBadge';
import { Check, X, SkipForward, ChevronLeft, ChevronRight, MapPin, Hash } from 'lucide-react';
import './ReviewQueue.css';

const STUB_QUEUE = [
  {
    id: 'q123', platform: 'facebook', author: 'stub_user_99', score: 0.92,
    content: 'اعوذ بالله من الشيطان الرجيم — هؤلاء القوم لا يستحقون العيش بيننا',
    parent_post: 'A post discussing Yazidi religious practices and cultural identity in Sinjar region.',
    target_group: 'Yazidi', case_title: 'Anti-Yazidi Campaign',
    tropes_fired: [{ name: 'Devil-worship trope', activation: 'Target group Yazidi present + devil reference' }],
    trace: [
      { agent: 'Triage', decision: 'FLAG', reasoning: 'Matched lexicon entry for dehumanization. Target group: Yazidi.' },
      { agent: 'Specialist', decision: 'FLAG', reasoning: 'Trope #42 activated: Devil-worship implicature on Yazidi-context post. High confidence (0.92).' },
      { agent: 'Critic', decision: 'ESCALATE', reasoning: 'Context-dependent hate speech. Trope activation confirmed. Recommend human review.' },
    ]
  },
  {
    id: 'q124', platform: 'tiktok', author: 'hateful_commenter', score: 0.87,
    content: 'These people should be expelled from our country. They don\'t belong here.',
    parent_post: 'TikTok video about the Shabak minority community in Nineveh.',
    target_group: 'Shabak', case_title: 'Shabak Community Targeting',
    tropes_fired: [],
    trace: [
      { agent: 'Triage', decision: 'FLAG', reasoning: 'Explicit exclusionary language detected.' },
      { agent: 'Specialist', decision: 'FLAG', reasoning: 'Direct call for expulsion targeting Shabak group. Severity: High.' },
      { agent: 'Critic', decision: 'ESCALATE', reasoning: 'Clear hate speech. No ambiguity.' },
    ]
  },
  {
    id: 'q125', platform: 'instagram', author: 'concerned_citizen', score: 0.61,
    content: 'الله يعين هل ناس — بس شنو نسوي؟',
    parent_post: 'Instagram post about Christian families leaving Mosul after threats.',
    target_group: 'Christian', case_title: 'Christian Displacement',
    tropes_fired: [],
    trace: [
      { agent: 'Triage', decision: 'PASS', reasoning: 'No lexicon hits. Sentiment appears sympathetic.' },
      { agent: 'Specialist', decision: 'BORDERLINE', reasoning: 'Ambiguous — could be sympathetic or dismissive. Borderline confidence (0.61).' },
      { agent: 'Critic', decision: 'ESCALATE', reasoning: 'Genuinely ambiguous. Human judgment needed for cultural context.' },
    ]
  },
];

const ReviewQueue = () => {
  const [queue, setQueue] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);

  useEffect(() => {
    // Would fetch from /api/review/queue
    setTimeout(() => setQueue(STUB_QUEUE), 600);
  }, []);

  const queueItem = queue[currentIndex];

  if (queue.length === 0) {
    return <div className="loading-state">Loading queue...</div>;
  }

  const handleAction = (action) => {
    // Would POST to /api/review/{queue_item_id}/submit
    console.log(`${action} item ${queueItem.id}`);
    if (currentIndex < queue.length - 1) {
      setCurrentIndex(currentIndex + 1);
    }
  };

  return (
    <div className="review-queue animate-fade-in">
      <header className="page-header">
        <div>
          <h1>Review Queue</h1>
          <p className="page-subtitle">Human-in-the-loop decision gate</p>
        </div>
        <div className="queue-nav">
          <button
            className="queue-nav-btn"
            disabled={currentIndex === 0}
            onClick={() => setCurrentIndex(currentIndex - 1)}
          >
            <ChevronLeft size={18} />
          </button>
          <span className="queue-position">
            {currentIndex + 1} / {queue.length}
          </span>
          <button
            className="queue-nav-btn"
            disabled={currentIndex === queue.length - 1}
            onClick={() => setCurrentIndex(currentIndex + 1)}
          >
            <ChevronRight size={18} />
          </button>
        </div>
      </header>

      <div className="review-workspace">
        <div className="content-panel glass-panel">
          <div className="content-header">
            <span className={`platform-badge ${queueItem.platform}`}>{queueItem.platform}</span>
            <span className="author">@{queueItem.author}</span>
            <span className="score">Confidence: {(queueItem.score * 100).toFixed(0)}%</span>
          </div>

          {/* Parent Post Context (FR-RV-1: always visible) */}
          <div className="parent-post-context">
            <span className="context-label">Parent Post Context</span>
            <p className="parent-text">{queueItem.parent_post}</p>
          </div>

          {/* Flagged Content */}
          <div className="content-body">
            <span className="context-label">Flagged Content</span>
            <p className="post-text" dir={/[\u0600-\u06FF]/.test(queueItem.content) ? 'rtl' : 'ltr'}>
              {queueItem.content}
            </p>
          </div>

          {/* Target Group + Trope Info */}
          <div className="review-context-row">
            <div className="context-chip">
              <MapPin size={14} />
              <span>Target: {queueItem.target_group}</span>
            </div>
            {queueItem.case_title && (
              <div className="context-chip case-chip">
                <Hash size={14} />
                <span>{queueItem.case_title}</span>
              </div>
            )}
          </div>

          {queueItem.tropes_fired?.length > 0 && (
            <div className="tropes-section">
              {queueItem.tropes_fired.map((trope, i) => (
                <div key={i} className="trope-alert">
                  <span className="trope-name">⚠️ {trope.name}</span>
                  <span className="trope-activation">Activation: {trope.activation}</span>
                </div>
              ))}
            </div>
          )}

          <div className="action-bar">
            <button className="btn-reject" onClick={() => handleAction('reject')}>
              <X size={20}/> Reject (False Positive)
            </button>
            <button className="btn-skip" onClick={() => handleAction('skip')}>
              <SkipForward size={20}/> Skip
            </button>
            <button className="btn-confirm" onClick={() => handleAction('confirm')}>
              <Check size={20}/> Confirm Flag
            </button>
          </div>
        </div>

        <div className="trace-panel">
          <TraceLog traceData={queueItem.trace} />
        </div>
      </div>
    </div>
  );
};

export default ReviewQueue;
