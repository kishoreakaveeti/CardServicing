import React from 'react';
import { TraceInfo } from '../types';

interface DevTracePanelProps {
  isOpen: boolean;
  onClose: () => void;
  trace: TraceInfo | null;
}

export const DevTracePanel: React.FC<DevTracePanelProps> = ({ isOpen, onClose, trace }) => {
  if (!isOpen) return null;

  return (
    <div className="trace-panel-drawer" role="dialog" aria-label="AI Showcase Trace Inspector">
      <div className="trace-panel-header">
        <h2>
          <span>🔍</span> AI Showcase Inspector
        </h2>
        <button
          onClick={onClose}
          style={{
            background: 'transparent',
            border: 'none',
            color: '#94a3b8',
            fontSize: '1.2rem',
            cursor: 'pointer',
            padding: '4px 8px',
          }}
        >
          ✕
        </button>
      </div>

      <div className="trace-panel-body">
        {!trace ? (
          <div style={{ textAlign: 'center', padding: '40px 10px', color: '#94a3b8' }}>
            <div style={{ fontSize: '2rem', marginBottom: '8px' }}>⚡</div>
            <p style={{ fontWeight: 600, color: '#f8fafc', marginBottom: '4px' }}>No Trace Recorded Yet</p>
            <p style={{ fontSize: '0.8rem' }}>
              Send a request in the AI Assistant (such as the Sydney or North Korea scenario) to generate an end-to-end execution trace.
            </p>
          </div>
        ) : (
          <>
            {/* Telemetry Summary */}
            <div className="trace-section">
              <div className="trace-label">Execution Telemetry</div>
              <div className="metrics-grid">
                <div className="metric-box">
                  <span className="metric-title">E2E Latency</span>
                  <span className="metric-num">{trace.telemetry.e2e_latency_ms} ms</span>
                </div>
                <div className="metric-box">
                  <span className="metric-title">LLM Latency</span>
                  <span className="metric-num">{trace.telemetry.llm_latency_ms} ms</span>
                </div>
                <div className="metric-box">
                  <span className="metric-title">RAG Latency</span>
                  <span className="metric-num">{trace.telemetry.rag_latency_ms} ms</span>
                </div>
                <div className="metric-box">
                  <span className="metric-title">Est. Cost</span>
                  <span className="metric-num" style={{ color: '#4ade80' }}>
                    {trace.telemetry.estimated_cost_usd}
                  </span>
                </div>
              </div>
              <div style={{ marginTop: '8px', fontSize: '0.75rem', color: '#94a3b8', display: 'flex', justifyContent: 'space-between' }}>
                <span>Input tokens: {trace.telemetry.input_tokens}</span>
                <span>Output tokens: {trace.telemetry.output_tokens}</span>
                <span>Total: {trace.telemetry.total_tokens}</span>
              </div>
            </div>

            {/* Sanitized Customer Request */}
            <div className="trace-section">
              <div className="trace-label">Sanitized Customer Request</div>
              <p style={{ color: '#e2e8f0', fontSize: '0.85rem', fontStyle: 'italic', background: '#0f172a', padding: '8px 10px', borderRadius: '4px' }}>
                "{trace.customer_request}"
              </p>
            </div>

            {/* Natural Language Intent & Sentiment */}
            <div className="trace-section">
              <div className="trace-label">Intent &amp; Sentiment Understanding</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
                <div>
                  <span style={{ color: '#94a3b8' }}>Primary Intent: </span>
                  <span className="trace-val-pill">{trace.intent.primary}</span>
                </div>
                {trace.intent.secondary && trace.intent.secondary.length > 0 && (
                  <div>
                    <span style={{ color: '#94a3b8' }}>Secondary: </span>
                    {trace.intent.secondary.map((s) => (
                      <span key={s} className="trace-val-pill" style={{ marginRight: '4px' }}>
                        {s}
                      </span>
                    ))}
                  </div>
                )}
                <div style={{ display: 'flex', gap: '10px', marginTop: '4px' }}>
                  <div>
                    <span style={{ color: '#94a3b8' }}>Sentiment: </span>
                    <span
                      style={{
                        color:
                          trace.sentiment === 'upset'
                            ? '#f87171'
                            : trace.sentiment === 'worried'
                            ? '#fbbf24'
                            : trace.sentiment === 'delighted'
                            ? '#4ade80'
                            : '#93c5fd',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        fontSize: '0.8rem',
                      }}
                    >
                      {trace.sentiment}
                    </span>
                  </div>
                  <div>
                    <span style={{ color: '#94a3b8' }}>Urgency: </span>
                    <span
                      style={{
                        color: trace.urgency === 'high' ? '#f87171' : trace.urgency === 'elevated' ? '#fbbf24' : '#94a3b8',
                        fontWeight: 700,
                        textTransform: 'uppercase',
                        fontSize: '0.8rem',
                      }}
                    >
                      {trace.urgency}
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Geographic Inference */}
            <div className="trace-section">
              <div className="trace-label">Geographic Inference (Customer Declaration Only)</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div>
                  <span style={{ color: '#94a3b8' }}>Declared Location: </span>
                  <span style={{ color: '#f8fafc', fontWeight: 600 }}>{trace.country_inference.declared_location}</span>
                </div>
                <div>
                  <span style={{ color: '#94a3b8' }}>Normalized Country: </span>
                  <span style={{ color: '#38bdf8', fontWeight: 600 }}>
                    {trace.country_inference.country} ({trace.country_inference.replacement_country_code})
                  </span>
                </div>
                <div style={{ fontSize: '0.75rem', color: '#64748b', marginTop: '4px' }}>
                  🔒 No GPS, device coordinates, or IP geolocation used.
                </div>
              </div>
            </div>

            {/* Policy Evaluation & RAG Retrieval */}
            <div className="trace-section">
              <div className="trace-label">Deterministic Policy Engine &amp; RAG</div>
              <div style={{ marginBottom: '8px' }}>
                <span style={{ color: '#94a3b8' }}>Retrieved Policies:</span>
                <ul style={{ listStyle: 'none', paddingLeft: 0, marginTop: '4px', display: 'flex', flexDirection: 'column', gap: '4px' }}>
                  {trace.retrieved_policies.map((p) => (
                    <li key={p.id} style={{ fontSize: '0.75rem', color: '#cbd5e1', display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <span className="trace-val-pill" style={{ fontSize: '0.7rem' }}>
                        {p.id} v{p.version}
                      </span>
                      <span>{p.title}</span>
                    </li>
                  ))}
                </ul>
              </div>

              <div style={{ borderTop: '1px solid #334155', paddingTop: '8px' }}>
                <span style={{ color: '#94a3b8' }}>Policy Engine Decision:</span>
                <div style={{ display: 'flex', gap: '8px', marginTop: '6px' }}>
                  <span
                    className={`trace-val-pill ${
                      trace.policy_evaluation.block_permitted ? 'trace-badge-allowed' : 'trace-badge-denied'
                    }`}
                  >
                    Card Block: {trace.policy_evaluation.block_permitted ? 'PERMITTED' : 'DENIED'}
                  </span>
                  <span
                    className={`trace-val-pill ${
                      trace.policy_evaluation.replacement_permitted ? 'trace-badge-allowed' : 'trace-badge-denied'
                    }`}
                  >
                    Replacement: {trace.policy_evaluation.replacement_permitted ? 'PERMITTED' : 'DENIED'}
                  </span>
                </div>
                <div style={{ marginTop: '6px' }}>
                  <span className="trace-val-pill" style={{ fontSize: '0.7rem' }}>
                    Promotions: {trace.policy_evaluation.promotions_permitted ? 'PERMITTED' : 'SUPPRESSED'}
                  </span>
                </div>
              </div>
            </div>

            {/* Agent & Tool Orchestration */}
            <div className="trace-section">
              <div className="trace-label">LangGraph Agent &amp; MCP Tool Selection</div>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                <div>
                  <span style={{ color: '#94a3b8' }}>Specialist Agent: </span>
                  <span style={{ color: '#2dd4bf', fontWeight: 600 }}>{trace.selected_agent}</span>
                </div>
                <div>
                  <span style={{ color: '#94a3b8' }}>MCP Tool Candidate: </span>
                  <span className="trace-val-pill">{trace.selected_tool}</span>
                </div>
                <div>
                  <span style={{ color: '#94a3b8' }}>Human Confirmation: </span>
                  <span style={{ color: trace.human_approval.required ? '#fbbf24' : '#94a3b8', fontWeight: 600 }}>
                    {trace.human_approval.status}
                  </span>
                </div>
                <div>
                  <span style={{ color: '#94a3b8' }}>Backend Outcome: </span>
                  <span style={{ color: '#4ade80', fontWeight: 600 }}>{trace.backend_outcome}</span>
                </div>
              </div>
            </div>

            {/* Compact Conversation Summary */}
            <div className="trace-section">
              <div className="trace-label">Conversational Memory Summary</div>
              <p style={{ color: '#cbd5e1', fontSize: '0.8rem', fontStyle: 'italic' }}>
                "{trace.conversation_summary}"
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  );
};
