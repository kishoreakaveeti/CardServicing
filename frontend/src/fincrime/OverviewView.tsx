import React from 'react';
import { FinCrimeCase, PolicyRelease, QdrantStatus } from './types';

interface OverviewViewProps {
  cases: FinCrimeCase[];
  activeRelease: PolicyRelease | null;
  qdrantStatus: QdrantStatus | null;
  onNavigateTab: (tab: 'cases' | 'policies' | 'audit') => void;
  onReviewCase: (caseItem: FinCrimeCase) => void;
}

export const OverviewView: React.FC<OverviewViewProps> = ({
  cases,
  activeRelease,
  qdrantStatus,
  onNavigateTab,
  onReviewCase,
}) => {
  const safeCases = Array.isArray(cases) ? cases : [];
  const pendingCases = safeCases.filter((c) => c && c.status === 'PENDING');
  const approvedCases = safeCases.filter((c) => c && (c.status === 'APPROVED_PENDING_EXECUTION' || c.status === 'PROCESSING' || c.status === 'COMPLETED'));
  const rejectedCases = safeCases.filter((c) => c && c.status === 'REJECTED');

  return (
    <div>
      <div style={{ marginBottom: '24px' }}>
        <h2 style={{ fontSize: '1.5rem', fontWeight: 700, color: '#f8fafc' }}>
          FinCrime Operations & Governance Dashboard
        </h2>
        <p style={{ color: '#94a3b8', fontSize: '0.9rem', marginTop: '4px' }}>
          Real-time human-in-the-loop oversight, deterministic OPA policy evaluation, and dynamic vector knowledge base status.
        </p>
      </div>

      {/* Metrics Row */}
      <div className="fincrime-grid-cards">
        <div className="fincrime-stat-card" style={{ borderLeft: '4px solid #f59e0b' }}>
          <span className="label">Pending Approvals</span>
          <span className="value" style={{ color: '#f59e0b' }}>{pendingCases.length}</span>
          <span className="subtitle">LangGraph workflow interrupts awaiting staff</span>
        </div>

        <div className="fincrime-stat-card" style={{ borderLeft: '4px solid #10b981' }}>
          <span className="label">Approved Cases</span>
          <span className="value" style={{ color: '#10b981' }}>{approvedCases.length}</span>
          <span className="subtitle">Resumed & issued through Cards API</span>
        </div>

        <div className="fincrime-stat-card" style={{ borderLeft: '4px solid #ef4444' }}>
          <span className="label">Rejected Cases</span>
          <span className="value" style={{ color: '#ef4444' }}>{rejectedCases.length}</span>
          <span className="subtitle">Denied by FinCrime staff</span>
        </div>

        <div className="fincrime-stat-card" style={{ borderLeft: '4px solid #3b82f6' }}>
          <span className="label">Active Policy Release</span>
          <span className="value" style={{ fontSize: '1.25rem', color: '#60a5fa' }}>
            {activeRelease?.release_id || 'POL-006-R1'}
          </span>
          <span className="subtitle">Version {activeRelease?.version || '1.2.0'} (Live)</span>
        </div>

        <div className="fincrime-stat-card" style={{ borderLeft: '4px solid #8b5cf6' }}>
          <span className="label">Qdrant Vector DB</span>
          <span className="value" style={{ fontSize: '1.25rem', color: '#a78bfa' }}>
            {qdrantStatus?.connected ? 'Connected' : 'Active'}
          </span>
          <span className="subtitle">
            {qdrantStatus?.points_count !== undefined ? `${qdrantStatus.points_count} points indexed` : 'FastEmbed synced'}
          </span>
        </div>
      </div>

      {/* Pending Approvals Table */}
      <div className="fincrime-card-panel">
        <div className="fincrime-panel-title">
          <span>Priority Cases Requiring Review</span>
          <button className="fincrime-btn-secondary" onClick={() => onNavigateTab('cases')}>
            View All ({cases.length})
          </button>
        </div>

        {pendingCases.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '36px', color: '#64748b' }}>
            <div style={{ fontSize: '2rem', marginBottom: '8px' }}>🛡️</div>
            <p style={{ fontWeight: 600, color: '#94a3b8' }}>No pending approval cases</p>
            <p style={{ fontSize: '0.85rem' }}>All overseas card replacement workflows are clear.</p>
          </div>
        ) : (
          <table className="fincrime-table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Customer</th>
                <th>Action</th>
                <th>Country</th>
                <th>Risk Level</th>
                <th>Created</th>
                <th>Action</th>
              </tr>
            </thead>
            <tbody>
              {pendingCases.slice(0, 5).map((c) => (
                <tr key={c.case_id}>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{c.case_id}</td>
                  <td>{c.customer_id}</td>
                  <td><span style={{ textTransform: 'capitalize' }}>{(c.action || 'replacement').replace(/_/g, ' ')}</span></td>
                  <td>
                    <span className="badge-country">{c.current_country}</span>
                  </td>
                  <td>
                    <span style={{ color: '#f59e0b', fontWeight: 600 }}>{c.risk_level || 'ELEVATED'}</span>
                  </td>
                  <td style={{ color: '#94a3b8', fontSize: '0.8rem' }}>
                    {new Date(c.created_at).toLocaleTimeString()}
                  </td>
                  <td>
                    <button className="fincrime-btn-approve" onClick={() => onReviewCase(c)}>
                      Review & Decide
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* System Status and Quick Actions */}
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        <div className="fincrime-card-panel">
          <div className="fincrime-panel-title">Policy & Regulatory Posture</div>
          <p style={{ color: '#94a3b8', fontSize: '0.88rem', marginBottom: '14px' }}>
            POL-006 Geographic Replacement Restrictions is currently enforced with deterministic OPA precedence.
          </p>
          <div style={{ background: '#0f172a', padding: '12px', borderRadius: '8px', fontSize: '0.82rem', fontFamily: 'monospace', color: '#cbd5e1', marginBottom: '16px' }}>
            <div>Home Country: AU (Domestic - Auto-issue)</div>
            <div>Excluded Countries: {JSON.stringify(activeRelease?.rules_snapshot?.excluded_country_codes || ['KP'])}</div>
            <div>Overseas Review: Required for all non-excluded countries</div>
          </div>
          <button className="fincrime-btn-apply" onClick={() => onNavigateTab('policies')}>
            Open Policy Editor & Rules
          </button>
        </div>

        <div className="fincrime-card-panel">
          <div className="fincrime-panel-title">Human-in-the-Loop Resumption</div>
          <p style={{ color: '#94a3b8', fontSize: '0.88rem', marginBottom: '14px' }}>
            LangGraph checkpoints suspend workflows at the <code>fincrime_approval</code> node until an authorised staff member approves or rejects the case.
          </p>
          <ul style={{ color: '#94a3b8', fontSize: '0.85rem', paddingLeft: '20px', lineHeight: 1.8 }}>
            <li>Staff approval directly triggers <code>Command(resume=...)</code> in LangGraph</li>
            <li>Workflow re-validates current active policy before executing card replacement</li>
            <li>Customer refreshes their portal to receive the newly issued card</li>
          </ul>
        </div>
      </div>
    </div>
  );
};
