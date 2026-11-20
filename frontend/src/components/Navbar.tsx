import React from 'react';
import { Session } from '../types';

export type NavTab = 'home' | 'accounts' | 'cards' | 'chat' | 'activity';

interface NavbarProps {
  currentTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  session: Session | null;
  onSignOut: () => void;
  onOpenAuth: (mode: 'login' | 'register') => void;
  unreadCount?: number;
}

export const Navbar: React.FC<NavbarProps> = ({
  currentTab,
  onSelectTab,
  session,
  onSignOut,
  onOpenAuth,
}) => {
  return (
    <>
      <header className="top-nav">
        <div className="brand-wrapper" onClick={() => onSelectTab('home')} style={{ cursor: 'pointer' }}>
          <div className="brand-logo-icon">N</div>
          <div className="brand-text-block">
            <h1>Neo Bank</h1>
            <div className="brand-tagline">Banking, intelligently connected.</div>
          </div>
        </div>

        {session && (
          <nav className="nav-links-desktop" aria-label="Main Navigation">
            <button
              className={`nav-link ${currentTab === 'home' ? 'active' : ''}`}
              onClick={() => onSelectTab('home')}
            >
              <span>🏠</span> Home
            </button>
            <button
              className={`nav-link ${currentTab === 'accounts' ? 'active' : ''}`}
              onClick={() => onSelectTab('accounts')}
            >
              <span>🏦</span> Accounts
            </button>
            <button
              className={`nav-link ${currentTab === 'cards' ? 'active' : ''}`}
              onClick={() => onSelectTab('cards')}
            >
              <span>💳</span> Cards
            </button>
            <button
              className={`nav-link ${currentTab === 'chat' ? 'active' : ''}`}
              onClick={() => onSelectTab('chat')}
            >
              <span>💬</span> Assistant
            </button>
            <button
              className={`nav-link ${currentTab === 'activity' ? 'active' : ''}`}
              onClick={() => onSelectTab('activity')}
            >
              <span>📋</span> Activity
            </button>
          </nav>
        )}

        <div className="header-right">
          <a
            href="/fincrime"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              fontSize: '0.8rem',
              fontWeight: 600,
              color: '#ef4444',
              textDecoration: 'none',
              padding: '5px 10px',
              borderRadius: '6px',
              backgroundColor: '#fef2f2',
              border: '1px solid #fecaca',
            }}
            title="Open FinCrime Staff Portal in separate tab"
          >
            FinCrime Staff ↗
          </a>
          {session ? (
            <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
              <div className="user-badge" title="Authenticated Customer">
                <span>👤</span> {session.name}
              </div>
              <button className="sign-out-btn" onClick={onSignOut}>
                Sign out
              </button>
            </div>
          ) : (
            <div style={{ display: 'flex', gap: '8px' }}>
              <button className="btn-secondary" onClick={() => onOpenAuth('login')}>
                Sign in
              </button>
              <button className="btn-primary" onClick={() => onOpenAuth('register')}>
                Register
              </button>
            </div>
          )}
        </div>
      </header>

      {/* Mobile Responsive Bottom Navigation */}
      {session && (
        <nav className="bottom-nav-mobile" aria-label="Mobile Navigation">
          <button
            className={`bottom-nav-item ${currentTab === 'home' ? 'active' : ''}`}
            onClick={() => onSelectTab('home')}
          >
            <span className="bottom-nav-icon">🏠</span>
            <span>Home</span>
          </button>
          <button
            className={`bottom-nav-item ${currentTab === 'accounts' ? 'active' : ''}`}
            onClick={() => onSelectTab('accounts')}
          >
            <span className="bottom-nav-icon">🏦</span>
            <span>Accounts</span>
          </button>
          <button
            className={`bottom-nav-item ${currentTab === 'cards' ? 'active' : ''}`}
            onClick={() => onSelectTab('cards')}
          >
            <span className="bottom-nav-icon">💳</span>
            <span>Cards</span>
          </button>
          <button
            className={`bottom-nav-item ${currentTab === 'chat' ? 'active' : ''}`}
            onClick={() => onSelectTab('chat')}
          >
            <span className="bottom-nav-icon">💬</span>
            <span>Assistant</span>
          </button>
          <button
            className={`bottom-nav-item ${currentTab === 'activity' ? 'active' : ''}`}
            onClick={() => onSelectTab('activity')}
          >
            <span className="bottom-nav-icon">📋</span>
            <span>Activity</span>
          </button>
        </nav>
      )}
    </>
  );
};
