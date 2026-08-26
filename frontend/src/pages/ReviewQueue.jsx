import React, { useState, useEffect, useCallback } from 'react';
import TraceLog from '../components/TraceLog';
import StatusBadge from '../components/StatusBadge';
import { Check, X, SkipForward, ChevronLeft, ChevronRight, MapPin, Hash } from 'lucide-react';
import './ReviewQueue.css';
import { api, ApiError } from '../api';


const ReviewQueue = () => {
  const [queue, setQueue] = useState([]);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const data = await api.reviewQueue();
      // The API names the identifier queue_item_id; the rest of this page calls it id.
      setQueue((data.queue || []).map(item => ({ ...item, id: item.queue_item_id })));
      setCurrentIndex(0);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const queueItem = queue[currentIndex];

  if (loading) {
    return <div className="loading-state">Loading queue...</div>;
  }
  if (error) {
    return (
      <div className="loading-state">
        <p>{error}</p>
        <button onClick={load}>Retry</button>
      </div>
    );
  }
  if (queue.length === 0) {
    return <div className="loading-state">Nothing waiting for review.</div>;
  }

  // This used to console.log and advance, so a reviewer worked through the queue
  // believing their judgements were recorded. Nothing was written. Everything the
  // system exists to collect was being discarded at the last step.
  const handleAction = async (action) => {
    if (submitting || !queueItem) return;

    if (action === 'skip') {
      if (currentIndex < queue.length - 1) setCurrentIndex(currentIndex + 1);
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      await api.submitReview(queueItem.id, {
        reviewer_id: 'dashboard',
        is_confirmed: action === 'confirm',
      });
      // Drop the reviewed item rather than advancing past it: it is no longer in the
      // server's queue, and leaving it on screen invites reviewing it twice.
      setQueue(prev => prev.filter((_, i) => i !== currentIndex));
      setCurrentIndex(i => Math.min(i, Math.max(0, queue.length - 2)));
    } catch (err) {
      setError(err instanceof ApiError ? err.message : String(err));
    } finally {
      setSubmitting(false);
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
