import React from 'react';
import { AuditEntry } from './types';

interface AuditLogViewProps {
  entries: AuditEntry[];
  onRefresh: () => void;
}

export const AuditLogView: React.FC<AuditLogViewProps> = ({ entries, onRefresh }) => {
  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '20px' }}>
        <div>
          <h2 style={{ fontSize: '1.4rem', fontWeight: 700, color: '#f8fafc' }}>
            Immutable Governance & FinCrime Audit Trail
          </h2>
          <p style={{ color: '#94a3b8', fontSize: '0.88rem' }}>
            Complete traceability of human approvals, policy releases, OPA synchronisations, and LangGraph workflow resumptions.
          </p>
        </div>
        <button className="fincrime-btn-secondary" onClick={onRefresh}>
          Refresh Audit Log
        </button>
      </div>

      <div className="fincrime-card-panel">
        {entries.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '40px', color: '#64748b' }}>
            No audit events recorded yet.
          </div>
        ) : (
          <table className="fincrime-table">
            <thead>
              <tr>
                <th>Event ID</th>
                <th>Timestamp</th>
                <th>Event Type</th>
                <th>Actor</th>
                <th>Role</th>
                <th>Details</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((entry) => (
                <tr key={entry.event_id}>
                  <td style={{ fontFamily: 'monospace', fontSize: '0.8rem', color: '#94a3b8' }}>
                    {entry.event_id}
                  </td>
                  <td style={{ fontSize: '0.82rem', whiteSpace: 'nowrap' }}>
                    {new Date(entry.timestamp).toLocaleString()}
                  </td>
                  <td>
                    <span
                      style={{
                        padding: '2px 8px',
                        borderRadius: '6px',
                        fontSize: '0.78rem',
                        fontWeight: 700,
                        backgroundColor: entry.event_type.includes('APPROVED')
                          ? '#14532d'
                          : entry.event_type.includes('REJECTED')
                          ? '#7f1d1d'
                          : entry.event_type.includes('ACTIVATED')
                          ? '#1e3a8a'
                          : '#1e293b',
                        color: '#f8fafc',
                      }}
                    >
                      {entry.event_type}
                    </span>
                  </td>
                  <td style={{ fontWeight: 600 }}>{entry.actor}</td>
                  <td>
                    <span className="fincrime-role-tag">{entry.actor_role}</span>
                  </td>
                  <td style={{ fontSize: '0.82rem', color: '#cbd5e1', maxWidth: '350px' }}>
                    {entry.details ? JSON.stringify(entry.details) : '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
};
