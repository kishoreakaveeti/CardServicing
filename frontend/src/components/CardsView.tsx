import React, { useState } from 'react';
import { AccountRecord, CardRecord } from '../types';

interface CardsViewProps {
  cards: CardRecord[];
  accounts: AccountRecord[];
  isLoading: boolean;
  onRefresh: () => void;
  onIssueCard: (accountNumber: string, additionalCard: boolean) => Promise<void>;
  onBlockCard: (cardId: string, reason: string) => Promise<void>;
  onNavigateToAccounts: () => void;
  onStartChat: (initialPrompt?: string) => void;
}

export const CardsView: React.FC<CardsViewProps> = ({
  cards,
  accounts,
  isLoading,
  onRefresh,
  onIssueCard,
  onBlockCard,
  onNavigateToAccounts,
  onStartChat,
}) => {
  // Modal states
  const [showIssueModal, setShowIssueModal] = useState(false);
  const [showBlockModal, setShowBlockModal] = useState(false);

  // Form states
  const [issueAccount, setIssueAccount] = useState('');
  const [additionalCard, setAdditionalCard] = useState(false);
  const [blockCardId, setBlockCardId] = useState('');
  const [blockReason, setBlockReason] = useState('lost');

  // Async submission states
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [errorNotice, setErrorNotice] = useState<string | null>(null);
  const [successNotice, setSuccessNotice] = useState<string | null>(null);

  const activeCards = cards.filter((c) => c.status !== 'blocked');

  const formatMaskedCardNumber = (simNumber: string) => {
    if (!simNumber) return '•••• •••• •••• ••••';
    const last4 = simNumber.slice(-4).toUpperCase();
    return `•••• •••• •••• ${last4}`;
  };

  const computeExpiry = (createdAt?: string) => {
    if (!createdAt) return '12/29';
    try {
      const d = new Date(createdAt);
      const month = String(d.getMonth() + 1).padStart(2, '0');
      const year = String((d.getFullYear() + 3) % 100).padStart(2, '0');
      return `${month}/${year}`;
    } catch {
      return '12/29';
    }
  };

  const getAccountLabel = (acc: AccountRecord) => {
    const typeLabel = acc.account_type === 'transaction' ? 'Everyday Transaction' : 'Savings Account';
    const last4 = acc.account_number ? acc.account_number.slice(-4) : '0000';
    return `${typeLabel} •••• ${last4}`;
  };

  const handleOpenIssueModal = () => {
    setErrorNotice(null);
    setSuccessNotice(null);
    if (accounts.length > 0 && !issueAccount) {
      setIssueAccount(accounts[0].account_number);
    }
    setShowIssueModal(true);
  };

  const handleOpenBlockModal = (cardId?: string) => {
    setErrorNotice(null);
    setSuccessNotice(null);
    if (cardId) {
      setBlockCardId(cardId);
    } else if (activeCards.length > 0) {
      setBlockCardId(activeCards[0].id);
    }
    setBlockReason('lost');
    setShowBlockModal(true);
  };

  const handleIssueSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!issueAccount) {
      setErrorNotice('Please select an eligible account.');
      return;
    }
    setErrorNotice(null);
    setIsSubmitting(true);
    try {
      await onIssueCard(issueAccount, additionalCard);
      setSuccessNotice('New Visa Debit card issued successfully.');
      setShowIssueModal(false);
      onRefresh();
    } catch (err) {
      setErrorNotice(err instanceof Error ? err.message : 'Could not issue card. Please review account details.');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleBlockSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!blockCardId) {
      setErrorNotice('Please select a card to block.');
      return;
    }
    setErrorNotice(null);
    setIsSubmitting(true);
    try {
      await onBlockCard(blockCardId, blockReason);
      setSuccessNotice('Card has been successfully blocked.');
      setShowBlockModal(false);
      onRefresh();
    } catch (err) {
      setErrorNotice(err instanceof Error ? err.message : 'Failed to block card.');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div>
      <div className="section-header">
        <div>
          <h2 className="section-title">Payment Cards</h2>
          <p className="section-desc">
            Authorized Visa Debit cards linked to your verified accounts. Instant digital controls and 24/7 security.
          </p>
        </div>
        <div style={{ display: 'flex', gap: '10px' }}>
          <button className="btn-secondary" onClick={onRefresh} disabled={isLoading}>
            <span>🔄</span> Refresh
          </button>
          {activeCards.length > 0 && (
            <button className="btn-secondary" onClick={() => handleOpenBlockModal()}>
              <span>🛡️</span> Block Card
            </button>
          )}
          <button className="btn-primary" onClick={handleOpenIssueModal}>
            <span>+</span> Issue Card
          </button>
        </div>
      </div>

      {successNotice && (
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
          {successNotice}
        </div>
      )}

      {cards.length === 0 ? (
        <div
          className="surface-card"
          style={{ textAlign: 'center', padding: '48px 24px', color: '#64748b' }}
        >
          <div style={{ fontSize: '2.5rem', marginBottom: '12px' }}>💳</div>
          <h3 style={{ fontSize: '1.2rem', color: '#101828', marginBottom: '8px' }}>
            No payment cards found
          </h3>
          <p style={{ maxWidth: '440px', margin: '0 auto 20px', fontSize: '0.95rem' }}>
            Issue a Visa Debit card linked to your everyday account to enable contactless and secure payments.
          </p>
          <button className="btn-primary" onClick={handleOpenIssueModal}>
            Issue Your First Card
          </button>
        </div>
      ) : (
        <div className="payment-card-container">
          {cards.map((card) => {
            const isBlocked = card.status === 'blocked';
            const cardholderName = card.given_name || card.user_name || 'NEO CUSTOMER';
            const expiry = computeExpiry(card.created_at);

            return (
              <div
                key={card.id}
                className="surface-card"
                style={{
                  display: 'flex',
                  flexDirection: 'column',
                  gap: '16px',
                }}
              >
                {/* Visual Digital Payment Card */}
                <div className={`visa-card-card ${isBlocked ? 'blocked' : ''}`}>
                  <div className="card-top-row">
                    <div className="card-bank-name">
                      NEO <span>BANK</span>
                    </div>
                    <span className={`card-status-pill ${isBlocked ? 'blocked' : 'active'}`}>
                      {isBlocked ? 'Blocked' : 'Active'}
                    </span>
                  </div>

                  <div className="card-chip-row">
                    <div className="sim-chip" title="EMV Smart Chip"></div>
                    <svg className="contactless-wave" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                      <path d="M8.5 16.5a5 5 0 0 1 0-9" />
                      <path d="M12 19a8.5 8.5 0 0 0 0-14" />
                      <path d="M15.5 21.5a12 12 0 0 0 0-19" />
                    </svg>
                  </div>

                  <div className="card-number-masked">
                    {formatMaskedCardNumber(card.card_number)}
                  </div>

                  <div className="card-bottom-row">
                    <div className="card-holder-info">
                      <span className="card-label">Cardholder</span>
                      <span className="card-holder-name">{cardholderName}</span>
                    </div>

                    <div className="card-expiry-info">
                      <span className="card-label">Expires</span>
                      <span className="card-expiry-val">{expiry}</span>
                    </div>

                    <div className="visa-logo">VISA</div>
                  </div>
                </div>

                {/* Card Details & Action Controls */}
                <div>
                  <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '0.85rem', color: '#64748b', marginBottom: '12px' }}>
                    <span>Account: {card.account_number ? `••••${card.account_number.slice(-4)}` : 'Main'}</span>
                    <span>Visa Debit</span>
                  </div>

                  <div className="card-actions-row">
                    {!isBlocked ? (
                      <>
                        <button
                          className="btn-danger"
                          style={{ flex: 1, padding: '8px 12px', fontSize: '0.85rem' }}
                          onClick={() => handleOpenBlockModal(card.id)}
                        >
                          Block Card
                        </button>
                        <button
                          className="btn-secondary"
                          style={{ flex: 1, padding: '8px 12px', fontSize: '0.85rem' }}
                          onClick={() => onStartChat(`I lost my card ending in ${card.card_number.slice(-4)}. Please block and replace it.`)}
                        >
                          Block &amp; Replace
                        </button>
                      </>
                    ) : (
                      <button
                        className="btn-primary"
                        style={{ width: '100%', padding: '8px 12px', fontSize: '0.85rem' }}
                        onClick={() => onStartChat(`Please request a replacement for my blocked card ending in ${card.card_number.slice(-4)}.`)}
                      >
                        Request Replacement
                      </button>
                    )}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {/* Issue New Card Modal */}
      {showIssueModal && (
        <div className="modal-overlay" onClick={() => !isSubmitting && setShowIssueModal(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#101828', marginBottom: '8px' }}>
              Issue New Visa Debit Card
            </h3>
            <p style={{ fontSize: '0.9rem', color: '#64748b', marginBottom: '20px' }}>
              Select an eligible verified account to link with your new digital Visa Debit payment card.
            </p>

            {errorNotice && (
              <div style={{ padding: '10px 14px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', fontSize: '0.85rem', marginBottom: '16px' }}>
                {errorNotice}
              </div>
            )}

            {accounts.length === 0 ? (
              <div>
                <p style={{ fontSize: '0.95rem', color: '#64748b', marginBottom: '16px' }}>
                  No eligible accounts found. You must have an active Everyday Transaction or Savings account before issuing a card.
                </p>
                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px' }}>
                  <button type="button" className="btn-secondary" onClick={() => setShowIssueModal(false)}>
                    Close
                  </button>
                  <button
                    type="button"
                    className="btn-primary"
                    onClick={() => {
                      setShowIssueModal(false);
                      onNavigateToAccounts();
                    }}
                  >
                    Open an Account Now
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleIssueSubmit}>
                <div className="form-group">
                  <label htmlFor="issue-account-select">Select Account</label>
                  <select
                    id="issue-account-select"
                    className="form-control"
                    value={issueAccount}
                    onChange={(e) => setIssueAccount(e.target.value)}
                    disabled={isSubmitting}
                    required
                  >
                    {accounts.map((acc) => (
                      <option key={acc.account_number} value={acc.account_number}>
                        {getAccountLabel(acc)}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group" style={{ marginTop: '14px' }}>
                  <label htmlFor="card-product-select">Card Product</label>
                  <select id="card-product-select" className="form-control" disabled value="visa_debit">
                    <option value="visa_debit">Visa Debit (Digital &amp; Physical Linked)</option>
                  </select>
                </div>

                <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '10px', marginTop: '16px' }}>
                  <input
                    type="checkbox"
                    id="additional-card-check"
                    checked={additionalCard}
                    onChange={(e) => setAdditionalCard(e.target.checked)}
                    disabled={isSubmitting}
                    style={{ width: '18px', height: '18px', accentColor: '#0d9488' }}
                  />
                  <label htmlFor="additional-card-check" style={{ margin: 0, fontWeight: 500, fontSize: '0.85rem', cursor: 'pointer' }}>
                    Issue as additional card (if this account already has an active card)
                  </label>
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '24px' }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setShowIssueModal(false)}
                    disabled={isSubmitting}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn-primary" disabled={isSubmitting}>
                    {isSubmitting ? 'Issuing Card...' : 'Confirm & Issue Card'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}

      {/* Block Card Confirmation Modal */}
      {showBlockModal && (
        <div className="modal-overlay" onClick={() => !isSubmitting && setShowBlockModal(false)}>
          <div className="modal-dialog" onClick={(e) => e.stopPropagation()}>
            <h3 style={{ fontSize: '1.25rem', fontWeight: 700, color: '#101828', marginBottom: '8px' }}>
              Block Payment Card
            </h3>
            <p style={{ fontSize: '0.9rem', color: '#64748b', marginBottom: '16px' }}>
              Blocking your card takes effect immediately across all physical, digital, and online payment channels.
            </p>

            {errorNotice && (
              <div style={{ padding: '10px 14px', backgroundColor: '#fef2f2', border: '1px solid #fca5a5', borderRadius: '6px', color: '#991b1b', fontSize: '0.85rem', marginBottom: '16px' }}>
                {errorNotice}
              </div>
            )}

            {activeCards.length === 0 ? (
              <div>
                <p style={{ fontSize: '0.95rem', color: '#64748b', marginBottom: '16px' }}>
                  You do not have any active cards eligible to be blocked.
                </p>
                <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
                  <button type="button" className="btn-secondary" onClick={() => setShowBlockModal(false)}>
                    Close
                  </button>
                </div>
              </div>
            ) : (
              <form onSubmit={handleBlockSubmit}>
                <div className="form-group">
                  <label htmlFor="block-card-select">Card to Block</label>
                  <select
                    id="block-card-select"
                    className="form-control"
                    value={blockCardId}
                    onChange={(e) => setBlockCardId(e.target.value)}
                    disabled={isSubmitting}
                    required
                  >
                    {activeCards.map((c) => (
                      <option key={c.id} value={c.id}>
                        Visa Debit {formatMaskedCardNumber(c.card_number)} ({c.account_number ? `Acct •••• ${c.account_number.slice(-4)}` : 'Main'})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="form-group" style={{ marginTop: '14px' }}>
                  <label htmlFor="block-reason-select">Reason for Blocking</label>
                  <select
                    id="block-reason-select"
                    className="form-control"
                    value={blockReason}
                    onChange={(e) => setBlockReason(e.target.value)}
                    disabled={isSubmitting}
                    required
                  >
                    <option value="lost">Card Lost</option>
                    <option value="stolen">Card Stolen</option>
                    <option value="fraud">Suspected Fraud / Compromised</option>
                    <option value="freeze">Temporary Security Freeze</option>
                    <option value="damaged">Damaged / Destroyed</option>
                  </select>
                </div>

                <div
                  style={{
                    backgroundColor: '#fffbeb',
                    border: '1px solid #fcd34d',
                    borderRadius: '8px',
                    padding: '12px 14px',
                    marginTop: '16px',
                    fontSize: '0.85rem',
                    color: '#92400e',
                  }}
                >
                  ⚠️ <strong>Confirmation Required:</strong> Once confirmed, this card cannot be used for payments. If you require a replacement, you can request one immediately after.
                </div>

                <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '10px', marginTop: '24px' }}>
                  <button
                    type="button"
                    className="btn-secondary"
                    onClick={() => setShowBlockModal(false)}
                    disabled={isSubmitting}
                  >
                    Cancel
                  </button>
                  <button type="submit" className="btn-danger" disabled={isSubmitting}>
                    {isSubmitting ? 'Blocking Card...' : 'Confirm Block'}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      )}
    </div>
  );
};
