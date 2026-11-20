import React, { useState } from 'react';
import { FinCrimeCase } from './types';

interface CaseManagementViewProps {
  cases: FinCrimeCase[];
  selectedCase: FinCrimeCase | null;
  onSelectCase: (caseItem: FinCrimeCase | null) => void;
  onApprove: (caseId: string, reason: string) => Promise<void>;
  onReject: (caseId: string, reason: string) => Promise<void>;
  onRefresh: () => void;
  isSubmitting: boolean;
}

export const CaseManagementView: React.FC<CaseManagementViewProps> = ({
  cases,
  selectedCase,
  onSelectCase,
  onApprove,
  onReject,
  onRefresh,
  isSubmitting,
}) => {
  const [filter, setFilter] = useState<'ALL' | 'PENDING' | 'APPROVED' | 'REJECTED'>('PENDING');
  const [decisionReason, setDecisionReason] = useState('');
  const [errorNotice, setErrorNotice] = useState<string | null>(null);

  const filteredCases = cases.filter((c) => {
    if (filter === 'ALL') return true;
    if (filter === 'PENDING') return c.status === 'PENDING';
    if (filter === 'APPROVED') return c.status === 'APPROVED' || c.status === 'COMPLETED';
    if (filter === 'REJECTED') return c.status === 'REJECTED';
    return true;
  });

  const handleApprove = async () => {
    if (!selectedCase) return;
    if (!decisionReason.trim()) {
      setErrorNotice('Decision justification is mandatory for audit compliance.');
      return;
    }
    setErrorNotice(null);
    try {
      await onApprove(selectedCase.case_id, decisionReason);
      setDecisionReason('');
      onSelectCase(null);
    } catch (err: any) {
      setErrorNotice(err.message || 'Approval failed');
    }
  };

  const handleReject = async () => {
    if (!selectedCase) return;
    if (!decisionReason.trim()) {
      setErrorNotice('Decision justification is mandatory for audit compliance.');
      return;
    }
    setErrorNotice(null);
    try {
      await onReject(selectedCase.case_id, decisionReason);
      setDecisionReason('');
      onSelectCase(null);
    } catch (err: any) {
      setErrorNotice(err.message || 'Rejection failed');
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <div>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 700, color: '#f8fafc' }}>
            Case Management & Human Approval
          </h2>
          <p style={{ color: '#94a3b8', fontSize: '0.88rem' }}>
            Review flagged overseas card replacement requests suspended in LangGraph checkpoints.
          </p>
        </div>
        <button className="fincrime-btn-secondary" onClick={onRefresh}>
          Refresh Cases
        </button>
      </div>

      {/* Filter Tabs */}
      <div style={{ display: 'flex', gap: '8px', marginBottom: '16px' }}>
        {(['PENDING', 'APPROVED', 'REJECTED', 'ALL'] as const).map((tab) => (
          <button
            key={tab}
            className={`fincrime-tab-btn ${filter === tab ? 'active' : ''}`}
            onClick={() => setFilter(tab)}
          >
            {tab === 'PENDING' ? `Pending (${cases.filter((c) => c.status === 'PENDING').length})` : tab}
          </button>
        ))}
      </div>

      {/* Cases Table */}
      <div className="fincrime-card-panel">
        {filteredCases.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#64748b' }}>
            No cases match the selected filter.
          </div>
        ) : (
          <table className="fincrime-table">
            <thead>
              <tr>
                <th>Case ID</th>
                <th>Customer ID</th>
                <th>Card ID</th>
                <th>Action</th>
                <th>Country</th>
                <th>Status</th>
                <th>Risk Score</th>
                <th>Created At</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              {filteredCases.map((c) => (
                <tr key={c.case_id}>
                  <td style={{ fontFamily: 'monospace', fontWeight: 600 }}>{c.case_id}</td>
                  <td>{c.customer_id}</td>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8rem' }}>{c.card_id}</td>
                  <td>{c.action}</td>
                  <td>
                    <span className="badge-country">{c.current_country}</span>
                  </td>
                  <td>
                    {c.status === 'PENDING' && <span className="badge-pending">PENDING</span>}
                    {c.status === 'APPROVED' && <span className="badge-approved">APPROVED</span>}
                    {c.status === 'COMPLETED' && <span className="badge-completed">COMPLETED</span>}
                    {c.status === 'REJECTED' && <span className="badge-rejected">REJECTED</span>}
                    {c.status === 'FAILED' && <span className="badge-rejected">FAILED</span>}
                  </td>
                  <td>{c.risk_score}/100</td>
                  <td style={{ fontSize: '0.8rem', color: '#94a3b8' }}>
                    {new Date(c.created_at).toLocaleString()}
                  </td>
                  <td>
                    <button
                      className={c.status === 'PENDING' ? 'fincrime-btn-approve' : 'fincrime-btn-secondary'}
                      style={{ padding: '4px 10px', fontSize: '0.8rem' }}
                      onClick={() => onSelectCase(c)}
                    >
                      {c.status === 'PENDING' ? 'Review' : 'View'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {/* Review Case Modal / Drawer */}
      {selectedCase && (
        <div className="fincrime-modal-backdrop" onClick={() => onSelectCase(null)}>
          <div className="fincrime-modal-content" onClick={(e) => e.stopPropagation()}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
              <h3 style={{ fontSize: '1.25rem', fontWeight: 700 }}>
                Case Review: {selectedCase.case_id}
              </h3>
              <button
                style={{ background: 'none', border: 'none', color: '#94a3b8', fontSize: '1.5rem', cursor: 'pointer' }}
                onClick={() => onSelectCase(null)}
              >
                ✕
              </button>
            </div>

            {errorNotice && (
              <div style={{ background: '#7f1d1d', color: '#fecaca', padding: '10px 14px', borderRadius: '8px', marginBottom: '16px', fontSize: '0.88rem' }}>
                {errorNotice}
              </div>
            )}

            <div style={{ background: '#0f172a', padding: '16px', borderRadius: '8px', marginBottom: '20px', display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '12px', fontSize: '0.88rem' }}>
              <div><strong style={{ color: '#94a3b8' }}>Customer:</strong> {selectedCase.customer_id}</div>
              <div><strong style={{ color: '#94a3b8' }}>Card ID:</strong> <span style={{ fontFamily: 'monospace' }}>{selectedCase.card_id}</span></div>
              <div><strong style={{ color: '#94a3b8' }}>Home Country:</strong> AU (Australia)</div>
              <div>
                <strong style={{ color: '#94a3b8' }}>Declared Country:</strong>{' '}
                <span className="badge-country">{selectedCase.current_country}</span> (Overseas)
              </div>
              <div><strong style={{ color: '#94a3b8' }}>Status:</strong> {selectedCase.status}</div>
              <div><strong style={{ color: '#94a3b8' }}>Risk Level:</strong> {selectedCase.risk_level || 'ELEVATED'} ({selectedCase.risk_score}/100)</div>
            </div>

            <div style={{ marginBottom: '16px' }}>
              <h4 style={{ fontSize: '0.95rem', fontWeight: 600, color: '#f8fafc', marginBottom: '8px' }}>
                Escalation Reasons & Policy Citations
              </h4>
              <div style={{ background: '#0f172a', padding: '12px', borderRadius: '8px', fontSize: '0.85rem' }}>
                <p style={{ color: '#f59e0b', marginBottom: '6px' }}>
                  ⚠️ {selectedCase.reasons?.join(', ') || 'Overseas card replacement requires FinCrime staff approval.'}
                </p>
                <div style={{ color: '#94a3b8', fontSize: '0.8rem', marginTop: '8px', borderTop: '1px solid #334155', paddingTop: '8px' }}>
                  <strong>Applicable Policy:</strong> POL-006 Geographic Replacement Restrictions<br />
                  <strong>RAG Vector Evidence:</strong> "Any replacement request for an active overseas cardholder outside Australia requires FinCrime human approval prior to card issuance."
                </div>
              </div>
            </div>

            {selectedCase.status === 'PENDING' ? (
              <div>
                <label style={{ display: 'block', fontSize: '0.88rem', fontWeight: 600, marginBottom: '6px' }}>
                  Decision Justification <span style={{ color: '#ef4444' }}>*</span>
                </label>
                <textarea
                  className="fincrime-textarea"
                  rows={3}
                  placeholder="Enter detailed justification for approval or rejection (logged in immutable audit trail)..."
                  value={decisionReason}
                  onChange={(e) => setDecisionReason(e.target.value)}
                />

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '20px' }}>
                  <button
                    className="fincrime-btn-reject"
                    disabled={isSubmitting}
                    onClick={handleReject}
                  >
                    {isSubmitting ? 'Processing...' : 'Reject Replacement'}
                  </button>
                  <button
                    className="fincrime-btn-approve"
                    disabled={isSubmitting}
                    onClick={handleApprove}
                  >
                    {isSubmitting ? 'Resuming Workflow...' : 'Approve & Issue Replacement'}
                  </button>
                </div>
              </div>
            ) : (
              <div style={{ background: '#0f172a', padding: '16px', borderRadius: '8px', fontSize: '0.88rem' }}>
                <div style={{ fontWeight: 600, color: selectedCase.status === 'REJECTED' ? '#ef4444' : '#10b981', marginBottom: '6px' }}>
                  Decision Recorded: {selectedCase.status}
                </div>
                <div style={{ color: '#cbd5e1' }}>
                  <strong>Decided by:</strong> {selectedCase.decided_by || 'FinCrime Analyst'}<br />
                  <strong>Reason:</strong> {selectedCase.decision_reason || 'N/A'}<br />
                  <strong>Timestamp:</strong> {new Date(selectedCase.updated_at).toLocaleString()}
                </div>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
