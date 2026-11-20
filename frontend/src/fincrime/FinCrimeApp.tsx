import React, { useEffect, useState } from 'react';
import './fincrime.css';
import { AuditEntry, FinCrimeCase, PolicyMetadata, PolicyRelease, QdrantStatus, StaffSession } from './types';
import { OverviewView } from './OverviewView';
import { CaseManagementView } from './CaseManagementView';
import { PolicyManagementView } from './PolicyManagementView';
import { AuditLogView } from './AuditLogView';

export const FinCrimeApp: React.FC = () => {
  const [session, setSession] = useState<StaffSession | null>(null);
  const [loadingSession, setLoadingSession] = useState(true);
  const [currentTab, setCurrentTab] = useState<'overview' | 'cases' | 'policies' | 'audit'>('overview');

  // Login form state
  const [loginUsername, setLoginUsername] = useState('fincrime.analyst');
  const [loginPassword, setLoginPassword] = useState('analyst123');
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  // App data state
  const [cases, setCases] = useState<FinCrimeCase[]>([]);
  const [selectedCase, setSelectedCase] = useState<FinCrimeCase | null>(null);
  const [policies, setPolicies] = useState<PolicyMetadata[]>([]);
  const [activeRelease, setActiveRelease] = useState<PolicyRelease | null>(null);
  const [qdrantStatus, setQdrantStatus] = useState<QdrantStatus | null>(null);
  const [auditEntries, setAuditEntries] = useState<AuditEntry[]>([]);
  const [isSubmittingAction, setIsSubmittingAction] = useState(false);

  // Check existing session
  const checkSession = async () => {
    try {
      const res = await fetch('/api/fincrime/session');
      if (res.ok) {
        const data = await res.json();
        const isAuthenticated = Boolean(data.authenticated || data.is_staff || data.user_name || data.name);
        if (isAuthenticated) {
          const roles: string[] = Array.isArray(data.roles) ? data.roles : [];
          const sessionObj: StaffSession = {
            authenticated: true,
            is_staff: true,
            user_name: data.user_name || data.name || 'Staff User',
            roles: roles,
            csrf: data.csrf || '',
            is_admin: Boolean(data.is_admin || roles.includes('FinCrime Policy Administrator') || roles.includes('FinCrime Supervisor')),
            is_analyst: Boolean(data.is_analyst || roles.includes('FinCrime Analyst') || roles.includes('FinCrime Supervisor')),
          };
          setSession(sessionObj);
          return sessionObj;
        }
      }
      setSession(null);
    } catch {
      setSession(null);
    } finally {
      setLoadingSession(false);
    }
    return null;
  };

  useEffect(() => {
    checkSession();
  }, []);

  // Fetch data when session is active
  const refreshAllData = async () => {
    try {
      const [casesRes, policiesRes, qdrantRes, auditRes] = await Promise.all([
        fetch('/api/fincrime/cases'),
        fetch('/api/fincrime/policies'),
        fetch('/api/fincrime/qdrant/status'),
        fetch('/api/fincrime/audit'),
      ]);

      if (casesRes.ok) {
        const data = await casesRes.json();
        setCases(Array.isArray(data) ? data : data.cases || []);
      }
      if (policiesRes.ok) {
        const data = await policiesRes.json();
        setPolicies(data.policies || []);
        setActiveRelease(data.active_release || null);
      }
      if (qdrantRes.ok) {
        const data = await qdrantRes.json();
        setQdrantStatus(data);
      }
      if (auditRes.ok) {
        const data = await auditRes.json();
        setAuditEntries(Array.isArray(data) ? data : data.entries || []);
      }
    } catch {
      // Non-blocking
    }
  };

  useEffect(() => {
    if (session) {
      refreshAllData();
      const interval = setInterval(refreshAllData, 10000);
      return () => clearInterval(interval);
    }
  }, [session]);

  const handleCredentialsLogin = async (usernameOverride?: string, passwordOverride?: string) => {
    setIsLoggingIn(true);
    setLoginError(null);
    const u = usernameOverride || loginUsername;
    const p = passwordOverride || loginPassword;

    try {
      const res = await fetch('/auth/staff/login-credentials', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username: u, password: p }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Authentication failed');

      const sess = await checkSession();
      if (!sess) {
        // Construct session directly from successful login response
        const roles: string[] = Array.isArray(data.roles) ? data.roles : [];
        const fallbackSession: StaffSession = {
          authenticated: true,
          is_staff: true,
          user_name: data.user_name || data.name || u,
          roles: roles,
          csrf: data.csrf || '',
          is_admin: Boolean(data.is_admin || roles.includes('FinCrime Policy Administrator') || roles.includes('FinCrime Supervisor')),
          is_analyst: Boolean(data.is_analyst || roles.includes('FinCrime Analyst') || roles.includes('FinCrime Supervisor')),
        };
        setSession(fallbackSession);
      }
    } catch (err: any) {
      setLoginError(err.message || 'Staff sign in failed');
    } finally {
      setIsLoggingIn(false);
    }
  };

  const handleLogout = async () => {
    try {
      await fetch('/auth/staff/logout', { method: 'POST' });
    } catch {
      // Ignore
    }
    setSession(null);
  };

  const handleApproveCase = async (caseId: string, reason: string) => {
    setIsSubmittingAction(true);
    try {
      const res = await fetch(`/api/fincrime/cases/${caseId}/approve`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': session?.csrf || '',
        },
        body: JSON.stringify({ reason }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || data.detail || 'Approval execution failed');
      await refreshAllData();
    } finally {
      setIsSubmittingAction(false);
    }
  };

  const handleRejectCase = async (caseId: string, reason: string) => {
    setIsSubmittingAction(true);
    try {
      const res = await fetch(`/api/fincrime/cases/${caseId}/reject`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-CSRF-Token': session?.csrf || '',
        },
        body: JSON.stringify({ reason }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || 'Rejection failed');
      await refreshAllData();
    } finally {
      setIsSubmittingAction(false);
    }
  };

  const handleRetryCase = async (caseId: string, reason: string) => {
    setIsSubmittingAction(true);
    try {
      const res = await fetch(`/api/fincrime/cases/${caseId}/retry`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': session?.csrf || '' },
        body: JSON.stringify({ reason }),
      });
      const data = await res.json();
      if (!res.ok || data.ok === false) throw new Error(data.error || data.detail || 'Retry failed');
      await refreshAllData();
    } finally { setIsSubmittingAction(false); }
  };

  if (loadingSession) {
    return (
      <div className="fincrime-shell" style={{ alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: '#94a3b8' }}>Verifying FinCrime credentials...</p>
      </div>
    );
  }

  // Login view if unauthenticated
  if (!session) {
    return (
      <div className="fincrime-shell" style={{ alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
        <div style={{ maxWidth: '440px', width: '100%', backgroundColor: '#0f172a', border: '1px solid #1e293b', borderRadius: '16px', padding: '36px', boxShadow: '0 10px 25px -5px rgba(0,0,0,0.5)' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '12px', marginBottom: '24px' }}>
            <div className="fincrime-badge-icon">🛡️</div>
            <div>
              <h1 style={{ fontSize: '1.25rem', fontWeight: 800, color: '#f8fafc', letterSpacing: '-0.02em' }}>
                FinCrime Operations
              </h1>
              <p style={{ fontSize: '0.78rem', color: '#ef4444', fontWeight: 600, letterSpacing: '0.05em' }}>
                ENTERPRISE STAFF PORTAL
              </p>
            </div>
          </div>

          <p style={{ fontSize: '0.88rem', color: '#94a3b8', marginBottom: '20px' }}>
            Authorised access only. Role-based access control enforced via Keycloak.
          </p>

          {loginError && (
            <div style={{ background: '#7f1d1d', color: '#fecaca', padding: '10px 14px', borderRadius: '8px', marginBottom: '16px', fontSize: '0.85rem' }}>
              {loginError}
            </div>
          )}

          <div style={{ marginBottom: '14px' }}>
            <label style={{ fontSize: '0.82rem', fontWeight: 600, color: '#cbd5e1' }}>Staff Username</label>
            <input
              type="text"
              className="fincrime-input-text"
              value={loginUsername}
              onChange={(e) => setLoginUsername(e.target.value)}
            />
          </div>

          <div style={{ marginBottom: '20px' }}>
            <label style={{ fontSize: '0.82rem', fontWeight: 600, color: '#cbd5e1' }}>Password</label>
            <input
              type="password"
              className="fincrime-input-text"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
            />
          </div>

          <button
            className="fincrime-btn-apply"
            style={{ width: '100%', marginBottom: '16px' }}
            disabled={isLoggingIn}
            onClick={() => handleCredentialsLogin()}
          >
            {isLoggingIn ? 'Authenticating...' : 'Sign In with Staff Credentials'}
          </button>

          {/* Quick Demo Logins */}
          <div style={{ borderTop: '1px solid #1e293b', paddingTop: '16px', marginTop: '16px' }}>
            <span style={{ fontSize: '0.78rem', color: '#64748b', fontWeight: 600, textTransform: 'uppercase' }}>
              Quick Demo Identities:
            </span>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px', marginTop: '8px' }}>
              <button
                className="fincrime-btn-secondary"
                style={{ fontSize: '0.78rem', padding: '8px' }}
                onClick={() => handleCredentialsLogin('fincrime.analyst', 'analyst123')}
              >
                Analyst Login
              </button>
              <button
                className="fincrime-btn-secondary"
                style={{ fontSize: '0.78rem', padding: '8px' }}
                onClick={() => handleCredentialsLogin('fincrime.admin', 'admin123')}
              >
                Policy Admin
              </button>
            </div>
          </div>

          <div style={{ marginTop: '20px', textAlign: 'center' }}>
            <a
              href="/"
              style={{ color: '#64748b', fontSize: '0.82rem', textDecoration: 'none' }}
            >
              ← Back to Customer Banking App
            </a>
          </div>
        </div>
      </div>
    );
  }

  const pendingCount = cases.filter((c) => c.status === 'PENDING').length;

  return (
    <div className="fincrime-shell">
      {/* Top Staff Navigation */}
      <header className="fincrime-top-nav">
        <div className="fincrime-brand">
          <div className="fincrime-badge-icon">🛡️</div>
          <div>
            <div style={{ fontWeight: 800, fontSize: '1.05rem', color: '#f8fafc', lineHeight: 1.1 }}>
              NEO BANK <span style={{ color: '#ef4444' }}>FINCRIME</span>
            </div>
            <div style={{ fontSize: '0.7rem', color: '#94a3b8', fontWeight: 500 }}>
              Staff Operations & Governance
            </div>
          </div>
        </div>

        {/* Tab Controls */}
        <nav className="fincrime-tabs">
          <button
            className={`fincrime-tab-btn ${currentTab === 'overview' ? 'active' : ''}`}
            onClick={() => setCurrentTab('overview')}
          >
            Overview
          </button>
          <button
            className={`fincrime-tab-btn ${currentTab === 'cases' ? 'active' : ''}`}
            onClick={() => setCurrentTab('cases')}
          >
            Case Management {pendingCount > 0 && <span style={{ background: '#ef4444', color: 'white', borderRadius: '10px', padding: '1px 6px', fontSize: '0.72rem', marginLeft: '4px' }}>{pendingCount}</span>}
          </button>
          <button
            className={`fincrime-tab-btn ${currentTab === 'policies' ? 'active' : ''}`}
            onClick={() => setCurrentTab('policies')}
          >
            Policy & OPA Release
          </button>
          <button
            className={`fincrime-tab-btn ${currentTab === 'audit' ? 'active' : ''}`}
            onClick={() => setCurrentTab('audit')}
          >
            Audit Trail
          </button>
        </nav>

        {/* User Identity & Switcher */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div className="fincrime-user-pill">
            <span style={{ fontWeight: 600 }}>{session.user_name}</span>
            <span className="fincrime-role-tag">
              {session.is_admin ? 'Policy Admin' : 'Analyst'}
            </span>
          </div>

          <a
            href="/"
            target="_blank"
            rel="noopener noreferrer"
            style={{
              color: '#38bdf8',
              fontSize: '0.82rem',
              fontWeight: 600,
              textDecoration: 'none',
              padding: '6px 10px',
              borderRadius: '6px',
              background: '#0369a120',
              border: '1px solid #0284c740',
            }}
          >
            Customer App ↗
          </a>

          <button
            className="fincrime-btn-secondary"
            style={{ padding: '6px 12px', fontSize: '0.82rem' }}
            onClick={handleLogout}
          >
            Log Out
          </button>
        </div>
      </header>

      {/* Main Content Area */}
      <main className="fincrime-main-content">
        {currentTab === 'overview' && (
          <OverviewView
            cases={cases}
            activeRelease={activeRelease}
            qdrantStatus={qdrantStatus}
            onNavigateTab={(tab) => setCurrentTab(tab)}
            onReviewCase={(c) => {
              setSelectedCase(c);
              setCurrentTab('cases');
            }}
          />
        )}

        {currentTab === 'cases' && (
          <CaseManagementView
            cases={cases}
            selectedCase={selectedCase}
            onSelectCase={setSelectedCase}
            onApprove={handleApproveCase}
            onReject={handleRejectCase}
            onRetry={handleRetryCase}
            onRefresh={refreshAllData}
            isSubmitting={isSubmittingAction}
          />
        )}

        {currentTab === 'policies' && (
          <PolicyManagementView
            policies={policies}
            activeRelease={activeRelease}
            onRefresh={refreshAllData}
            isAdmin={session.is_admin}
          />
        )}

        {currentTab === 'audit' && (
          <AuditLogView entries={auditEntries} onRefresh={refreshAllData} />
        )}
      </main>
    </div>
  );
};
