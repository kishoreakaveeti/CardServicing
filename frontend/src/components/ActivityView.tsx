import React from 'react';
import { ActivityItem } from '../types';

interface ActivityViewProps {
  activities: ActivityItem[];
  onClear: () => void;
}

export const ActivityView: React.FC<ActivityViewProps> = ({ activities, onClear }) => {
  return (
    <div>
      <div className="section-header">
        <div>
          <h2 className="section-title">Activity Audit Trail</h2>
          <p className="section-desc">
            Immutable log of customer interactions, policy evaluations, and executed banking transactions.
          </p>
        </div>
        {activities.length > 0 && (
          <button className="btn-secondary" onClick={onClear}>
            Clear View
          </button>
        )}
      </div>

      {activities.length === 0 ? (
        <div className="surface-card" style={{ textAlign: 'center', padding: '40px', color: '#64748b' }}>
          <div style={{ fontSize: '2rem', marginBottom: '8px' }}>📋</div>
          <h3 style={{ fontSize: '1.1rem', color: '#101828', marginBottom: '4px' }}>No recent activity</h3>
          <p style={{ fontSize: '0.9rem' }}>Actions you take in the application or through the AI Assistant will appear here.</p>
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
          {activities.map((act) => {
            const iconMap: Record<string, string> = {
              security: '🛡️',
              card: '💳',
              account: '🏦',
              auth: '🔑',
            };
            return (
              <div
                key={act.id}
                className="surface-card"
                style={{ padding: '16px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
                  <div
                    style={{
                      width: '40px',
                      height: '40px',
                      borderRadius: '50%',
                      backgroundColor: '#f1f5f9',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '1.1rem',
                    }}
                  >
                    {iconMap[act.type] || '📌'}
                  </div>
                  <div>
                    <h4 style={{ fontSize: '0.95rem', fontWeight: 700, color: '#101828' }}>{act.title}</h4>
                    <p style={{ fontSize: '0.85rem', color: '#64748b' }}>{act.details}</p>
                  </div>
                </div>
                <div style={{ fontSize: '0.75rem', color: '#94a3b8', fontFamily: 'monospace' }}>
                  {act.timestamp}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
