import React from 'react';
import { NavTab } from './Navbar';
import { AccountRecord, ActivityItem, CardRecord, Session } from '../types';

interface HomeViewProps {
  session: Session;
  accounts: AccountRecord[];
  cards: CardRecord[];
  activities: ActivityItem[];
  onNavigate: (tab: NavTab) => void;
  onOpenAccount: () => void;
  onIssueCard: () => void;
}

export const HomeView: React.FC<HomeViewProps> = ({
  session,
  accounts,
  cards,
  activities,
  onNavigate,
  onOpenAccount,
  onIssueCard,
}) => {
  const activeCards = cards.filter((c) => c.status !== 'blocked');
  const blockedCards = cards.filter((c) => c.status === 'blocked');
  const transactionAccounts = accounts.filter((a) => a.account_type === 'transaction');
  const savingsAccounts = accounts.filter((a) => a.account_type === 'savings');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '24px' }}>
      {/* Hero Welcome Banner */}
      <div
        className="surface-card"
        style={{
          background: 'linear-gradient(135deg, #101828 0%, #1e293b 70%, #0f766e 100%)',
          color: 'white',
          padding: '32px',
          borderRadius: '12px',
        }}
      >
        <div style={{ maxWidth: '640px' }}>
          <h2 style={{ fontSize: '1.85rem', fontWeight: 800, marginBottom: '8px' }}>
            Welcome back, {session.name}
          </h2>
          <p style={{ color: '#cbd5e1', fontSize: '1.05rem', lineHeight: 1.5, marginBottom: '24px' }}>
            Banking, intelligently connected. Manage your transaction accounts, digital Visa Debit cards, and security settings seamlessly.
          </p>
          <div style={{ display: 'flex', gap: '12px', flexWrap: 'wrap' }}>
            <button
              className="btn-primary"
              style={{ backgroundColor: '#0d9488' }}
              onClick={() => onNavigate('chat')}
            >
              <span>💬</span> Ask Neo
            </button>
            <button
              className="btn-secondary"
              style={{ background: 'rgba(255, 255, 255, 0.1)', color: 'white', borderColor: 'rgba(255, 255, 255, 0.25)' }}
              onClick={() => onNavigate('cards')}
            >
              <span>💳</span> View Cards ({cards.length})
            </button>
            <button
              className="btn-secondary"
              style={{ background: 'rgba(255, 255, 255, 0.1)', color: 'white', borderColor: 'rgba(255, 255, 255, 0.25)' }}
              onClick={() => onNavigate('accounts')}
            >
              <span>🏦</span> View Accounts ({accounts.length})
            </button>
          </div>
        </div>
      </div>

      {/* Summary Cards Grid */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))', gap: '16px' }}>
        {/* Accounts Summary */}
        <div className="surface-card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('accounts')}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.85rem', color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>
              ACCOUNTS OVERVIEW
            </span>
            <span style={{ fontSize: '1.25rem' }}>🏦</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: 800, color: '#101828', margin: '10px 0 4px' }}>
            {accounts.length}
          </div>
          <div style={{ fontSize: '0.9rem', color: '#0d9488', fontWeight: 600 }}>
            {transactionAccounts.length} Everyday Transaction · {savingsAccounts.length} Savings
          </div>
          <div style={{ marginTop: '14px', paddingTop: '10px', borderTop: '1px solid #f1f5f9', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Balance protected by policy</span>
            <span style={{ fontSize: '0.85rem', color: '#0d9488', fontWeight: 600 }}>Manage →</span>
          </div>
        </div>

        {/* Cards Summary */}
        <div className="surface-card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('cards')}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.85rem', color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>
              PAYMENT CARDS
            </span>
            <span style={{ fontSize: '1.25rem' }}>💳</span>
          </div>
          <div style={{ fontSize: '2rem', fontWeight: 800, color: '#101828', margin: '10px 0 4px' }}>
            {cards.length}
          </div>
          <div style={{ fontSize: '0.9rem', color: activeCards.length > 0 ? '#16a34a' : '#64748b', fontWeight: 600 }}>
            {activeCards.length} Active {blockedCards.length > 0 && `· ${blockedCards.length} Blocked`}
          </div>
          <div style={{ marginTop: '14px', paddingTop: '10px', borderTop: '1px solid #f1f5f9', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.8rem', color: '#64748b' }}>Visa Debit Protection</span>
            <span style={{ fontSize: '0.85rem', color: '#0d9488', fontWeight: 600 }}>Manage →</span>
          </div>
        </div>

        {/* Security & Assistance Tile */}
        <div className="surface-card" style={{ cursor: 'pointer' }} onClick={() => onNavigate('chat')}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.85rem', color: '#64748b', fontWeight: 600, letterSpacing: '0.05em' }}>
              24/7 BANKING ASSISTANT
            </span>
            <span style={{ fontSize: '1.25rem' }}>🛡️</span>
          </div>
          <div style={{ fontSize: '1.3rem', fontWeight: 700, color: '#101828', margin: '10px 0 4px' }}>
            Neo Digital Care
          </div>
          <div style={{ fontSize: '0.9rem', color: '#64748b', lineHeight: 1.4 }}>
            Instant card blocking, replacement requests, and account services available 24/7.
          </div>
          <div style={{ marginTop: '14px', paddingTop: '10px', borderTop: '1px solid #f1f5f9', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontSize: '0.8rem', color: '#16a34a', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <span className="pulse-dot" style={{ backgroundColor: '#16a34a', width: '6px', height: '6px' }}></span> Available
            </span>
            <span style={{ fontSize: '0.85rem', color: '#0d9488', fontWeight: 600 }}>Ask Neo →</span>
          </div>
        </div>
      </div>

      {/* Quick Banking Actions Grid */}
      <div className="surface-card">
        <h3 style={{ fontSize: '1.15rem', fontWeight: 700, color: '#101828', marginBottom: '14px' }}>
          Quick Actions
        </h3>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: '12px' }}>
          <button
            className="btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '14px 16px', justifyContent: 'flex-start' }}
            onClick={() => onNavigate('accounts')}
          >
            <span style={{ fontSize: '1.3rem' }}>🏦</span>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontWeight: 600, color: '#101828', fontSize: '0.95rem' }}>View Accounts</div>
              <div style={{ fontSize: '0.8rem', color: '#64748b' }}>Check your verified accounts</div>
            </div>
          </button>

          <button
            className="btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '14px 16px', justifyContent: 'flex-start' }}
            onClick={() => onNavigate('cards')}
          >
            <span style={{ fontSize: '1.3rem' }}>💳</span>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontWeight: 600, color: '#101828', fontSize: '0.95rem' }}>View Cards</div>
              <div style={{ fontSize: '0.8rem', color: '#64748b' }}>Manage Visa Debit cards</div>
            </div>
          </button>

          <button
            className="btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '14px 16px', justifyContent: 'flex-start' }}
            onClick={onOpenAccount}
          >
            <span style={{ fontSize: '1.3rem' }}>➕</span>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontWeight: 600, color: '#101828', fontSize: '0.95rem' }}>Open an Account</div>
              <div style={{ fontSize: '0.8rem', color: '#64748b' }}>Transaction or Savings</div>
            </div>
          </button>

          <button
            className="btn-secondary"
            style={{ display: 'flex', alignItems: 'center', gap: '10px', padding: '14px 16px', justifyContent: 'flex-start' }}
            onClick={onIssueCard}
          >
            <span style={{ fontSize: '1.3rem' }}>✨</span>
            <div style={{ textAlign: 'left' }}>
              <div style={{ fontWeight: 600, color: '#101828', fontSize: '0.95rem' }}>Issue New Card</div>
              <div style={{ fontSize: '0.8rem', color: '#64748b' }}>Link to your account</div>
            </div>
          </button>
        </div>
      </div>

      {/* Recent Activity Summary */}
      <div className="surface-card">
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '14px' }}>
          <h3 style={{ fontSize: '1.15rem', fontWeight: 700, color: '#101828' }}>
            Recent Activity
          </h3>
          <button
            className="btn-secondary"
            style={{ padding: '6px 12px', fontSize: '0.85rem' }}
            onClick={() => onNavigate('activity')}
          >
            View All →
          </button>
        </div>

        {activities.length === 0 ? (
          <div style={{ textAlign: 'center', padding: '24px 0', color: '#94a3b8', fontSize: '0.9rem' }}>
            No recent activity recorded in this session.
          </div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {activities.slice(0, 4).map((item) => (
              <div
                key={item.id}
                style={{
                  display: 'flex',
                  justifyContent: 'space-between',
                  alignItems: 'center',
                  padding: '10px 14px',
                  borderRadius: '8px',
                  backgroundColor: '#f8fafc',
                  border: '1px solid #f1f5f9',
                }}
              >
                <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
                  <span style={{ fontSize: '1.1rem' }}>
                    {item.type === 'auth' ? '🔑' : item.type === 'card' ? '💳' : item.type === 'account' ? '🏦' : '🛡️'}
                  </span>
                  <div>
                    <div style={{ fontWeight: 600, fontSize: '0.9rem', color: '#101828' }}>{item.title}</div>
                    <div style={{ fontSize: '0.8rem', color: '#64748b' }}>{item.details}</div>
                  </div>
                </div>
                <span style={{ fontSize: '0.75rem', color: '#94a3b8' }}>{item.timestamp}</span>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
