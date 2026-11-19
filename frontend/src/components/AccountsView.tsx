import React, { useState } from 'react';
import { AccountRecord } from '../types';

interface AccountsViewProps {
  accounts: AccountRecord[];
  isLoading: boolean;
  onRefresh: () => void;
  onCreateAccount: (accountType: 'transaction' | 'savings', issueCard: boolean) => Promise<void>;
}

export const AccountsView: React.FC<AccountsViewProps> = ({
  accounts,
  isLoading,
  onRefresh,
  onCreateAccount,
}) => {
  const [showModal, setShowModal] = useState(false);
  const [selectedType, setSelectedType] = useState<'transaction' | 'savings'>('transaction');
  const [issueCard, setIssueCard] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setSuccessMsg(null);
    setIsSubmitting(true);
    try {
      await onCreateAccount(selectedType, issueCard);
      setSuccessMsg(`Successfully opened a new ${selectedType} account.`);
      setShowModal(false);
      onRefresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create account.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const maskAccountNumber = (acc: string) => {
    if (!acc) return '••••';
    if (acc.length <= 6) return `••••${acc.slice(-4)}`;
    return `${acc.slice(0, 4)}••••${acc.slice(-4)}`;
  };

  const getAccountDisplayName = (type: string) => {
    if (type.toLowerCase() === 'transaction') return 'Neo Everyday Transaction';
    if (type.toLowerCase() === 'savings') return 'Neo High-Interest Savings';
    return `Neo ${type.charAt(0).toUpperCase() + type.slice(1)}`;
  };

  return (
    <div>
      <div className="section-header">
        <div>
          <h2 className="section-title">My Accounts</h2>
          <p className="section-desc">
            Secure verified accounts with Neo Bank. Balance figures are omitted per security guidelines.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn-secondary" onClick={onRefresh} disabled={isLoading}>
            <span>🔄</span> Refresh
          </button>
          <button className="btn-primary" onClick={() => setShowModal(true)}>
            <span>+</span> Open Account
          </button>
        </div>
      </div>

      {successMsg && (
        <div
          style={{
            padding: '12px 16px',
            backgroundColor: '#f0fdf4',
            border: '1px solid #86efac',
            borderRadius: '8px',
            color: '#166534',
            fontSize: '0.9rem',
            marginBottom: '16px',
          }}
        >
          {successMsg}
        </div>
      )}

      {error && (
        <div
          style={{
            padding: '12px 16px',
            backgroundColor: '#fef2f2',
            border: '1px solid #fca5a5',
            borderRadius: '8px',
            color: '#991b1b',
            fontSize: '0.9rem',
            marginBottom: '16px',
          }}
        >
          {error}
        </div>
      )}

      {accounts.length === 0 ? (
        <div
          className="surface-card"
          style={{ textAlign: 'center', padding: '48px 24px', color: '#64748b' }}
        >
          <div style={{ fontSize: '2.5rem', marginBottom: '12px' }}>🏦</div>
          <h3 style={{ fontSize: '1.2rem', color: '#101828', marginBottom: '8px' }}>
            No accounts found
          </h3>
          <p style={{ maxWidth: '420px', margin: '0 auto 20px', fontSize: '0.95rem' }}>
            Open a Transaction or Savings account to begin issuing payment cards and conducting operations.
          </p>
          <button className="btn-primary" onClick={() => setShowModal(true)}>
            Open an Account Now
          </button>
        </div>
      ) : (
        <div className="account-card-grid">
          {accounts.map((acc) => (
            <div key={acc.account_number} className="account-item-card">
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start' }}>
                <span className="account-type-badge">
                  {acc.account_type}
                </span>
                <span className="account-status-tag">
                  <span className="pulse-dot" style={{ backgroundColor: '#16a34a', width: '6px', height: '6px' }}></span>
                  Active
                </span>
              </div>

              <div>
                <h4 style={{ fontSize: '1.05rem', fontWeight: 700, color: '#101828', marginBottom: '4px' }}>
                  {getAccountDisplayName(acc.account_type)}
                </h4>
                <div className="account-number-masked">
                  {maskAccountNumber(acc.account_number)}
                </div>
              </div>

              <div style={{ marginTop: 'auto', paddingTop: '12px', borderTop: '1px solid #f1f5f9', fontSize: '0.75rem', color: '#94a3b8' }}>
                Opened on {new Date(acc.created_at).toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Create Account Modal */}
      {showModal && (
        <div className="modal-overlay" onClick={() => !isSubmitting && setShowModal(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ fontSize: '1.3rem', fontWeight: 700, color: '#101828', marginBottom: '8px' }}>
              Open New Neo Account
            </h3>
            <p style={{ fontSize: '0.9rem', color: '#64748b', marginBottom: '20px' }}>
              Select an account type from the supported product catalogue. Duplicate submissions are protected by idempotency keys.
            </p>

            <form onSubmit={handleSubmit}>
              <div className="form-group">
                <label htmlFor="account-type-select">Account Type</label>
                <select
                  id="account-type-select"
                  className="form-control"
                  value={selectedType}
                  onChange={(e) => setSelectedType(e.target.value as 'transaction' | 'savings')}
                  disabled={isSubmitting}
                  required
                >
                  <option value="transaction">Transaction (Everyday Banking)</option>
                  <option value="savings">Savings (High Interest Savings)</option>
                </select>
              </div>

              <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '14px' }}>
                <input
                  type="checkbox"
                  id="issue-card-checkbox"
                  checked={issueCard}
                  onChange={(e) => setIssueCard(e.target.checked)}
                  disabled={isSubmitting}
                  style={{ width: '18px', height: '18px', accentColor: '#0d9488' }}
                />
                <label htmlFor="issue-card-checkbox" style={{ margin: 0, fontWeight: 500, fontSize: '0.9rem', cursor: 'pointer' }}>
                  Automatically issue a Visa Debit card with this account
                </label>
              </div>

              <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '12px', marginTop: '24px' }}>
                <button
                  type="button"
                  className="btn-secondary"
                  onClick={() => setShowModal(false)}
                  disabled={isSubmitting}
                >
                  Cancel
                </button>
                <button type="submit" className="btn-primary" disabled={isSubmitting}>
                  {isSubmitting ? 'Opening Account...' : 'Open Account'}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
};
