import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import {
  AccountRecord,
  ActivityItem,
  CardRecord,
  ChatMessage,
  InputTool,
  PendingAction,
  Reply,
  Session,
  TraceInfo,
} from './types';
import { Navbar, NavTab } from './components/Navbar';
import { HomeView } from './components/HomeView';
import { AccountsView } from './components/AccountsView';
import { CardsView } from './components/CardsView';
import { ChatView } from './components/ChatView';
import { ActivityView } from './components/ActivityView';

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [currentTab, setCurrentTab] = useState<NavTab>('home');
  const [cid, setCid] = useState('');
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [latestReply, setLatestReply] = useState<Reply | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<PendingAction | null>(null);
  const [accounts, setAccounts] = useState<AccountRecord[]>([]);
  const [cards, setCards] = useState<CardRecord[]>([]);
  const [activities, setActivities] = useState<ActivityItem[]>([]);
  const [currentTrace, setCurrentTrace] = useState<TraceInfo | null>(null);
  const [tools, setTools] = useState<InputTool[]>([]);
  const [tool, setTool] = useState('');
  const [authAction, setAuthAction] = useState<'register' | 'login' | ''>('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const usernameInputRef = useRef<HTMLInputElement | null>(null);

  // Load session on startup
  useEffect(() => {
    fetch('/api/session')
      .then((r) => (r.ok ? readSessionResponse(r) : null))
      .then((sess) => {
        setSession(sess);
        if (sess) {
          addActivity('Sign in', 'Session validated successfully', 'auth');
        }
      })
      .catch(() => setError('Session unavailable. Refresh to try again.'));
  }, []);

  // Restore conversation from sessionStorage when session is loaded
  useEffect(() => {
    if (session) {
      try {
        const savedCid = sessionStorage.getItem(`neobank_cid_${session.name}`);
        const savedMessages = sessionStorage.getItem(`neobank_chat_${session.name}`);
        if (savedCid) setCid(savedCid);
        if (savedMessages) {
          const parsed = JSON.parse(savedMessages);
          if (Array.isArray(parsed) && parsed.length > 0) {
            setMessages(parsed);
          }
        }
      } catch {
        // Non-blocking fallback
      }
    }
  }, [session?.name]);

  // Persist conversation to sessionStorage whenever messages or cid change
  useEffect(() => {
    if (session && cid) {
      try {
        sessionStorage.setItem(`neobank_cid_${session.name}`, cid);
        sessionStorage.setItem(`neobank_chat_${session.name}`, JSON.stringify(messages));
      } catch {
        // Non-blocking fallback
      }
    }
  }, [session?.name, cid, messages]);

  // Fetch accounts & cards when session is active
  const refreshBankingData = async () => {
    if (!session) return;
    try {
      const [accRes, cardRes] = await Promise.all([
        fetch('/api/accounts'),
        fetch('/api/cards'),
      ]);
      if (accRes.ok) {
        const data = await accRes.json();
        const list = Array.isArray(data) ? data : (data.accounts || []);
        setAccounts(list);
      }
      if (cardRes.ok) {
        const data = await cardRes.json();
        const list = Array.isArray(data) ? data : (data.cards || []);
        setCards(list);
      }
    } catch {
      // Non-blocking fallback
    }
  };

  useEffect(() => {
    if (session) {
      refreshBankingData();
    }
  }, [session]);

  const addActivity = (title: string, details: string, type: 'security' | 'card' | 'account' | 'auth') => {
    const item: ActivityItem = {
      id: crypto.randomUUID(),
      title,
      details,
      type,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' }),
    };
    setActivities((prev) => [item, ...prev.slice(0, 40)]);
  };

  // Dynamically refresh input options when conversation changes, active tool is required, or accounts/cards update
  const refreshInputs = async () => {
    if (!cid) return;
    try {
      const res = await fetch(`/api/conversations/${cid}/inputs`);
      if (res.ok) {
        const data = await res.json();
        setTools(data.tools || []);
      }
    } catch {
      // Non-blocking fallback
    }
  };

  useEffect(() => {
    if (cid) {
      refreshInputs();
    }
  }, [cid, latestReply?.input_tool, accounts.length, cards.length]);

  // Unified Request Dispatcher
  async function request(action: PendingAction) {
    setBusy(true);
    setError('');
    setPending(action);
    try {
      const response = await fetch(action.path, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(session ? { 'X-CSRF-Token': session.csrf } : {}),
          'Idempotency-Key': action.key,
        },
        body: JSON.stringify(action.body),
      });

      if (!response.ok) {
        if (response.status >= 400 && response.status < 500) setPending(null);
        if (response.status === 401) setSession(null);
        throw new Error(
          response.status === 401
            ? 'Sign in again to continue.'
            : response.status < 500
            ? 'Request details were rejected. Please review your input.'
            : 'Banking service unavailable. You may retry.'
        );
      }

      let value: Reply;
      if (response.status === 202) {
        let job = await response.json();
        while (job.status === 'pending' || job.status === 'running') {
          await new Promise((resolve) => setTimeout(resolve, 500));
          const statusRes = await fetch(job.status_url);
          if (!statusRes.ok) throw new Error('Polling interrupted. Please retry.');
          job = await statusRes.json();
        }
        if (job.status === 'failed' || !job.result) {
          setPending(null);
          throw new Error(job.error || 'Operation failed.');
        }
        value = job.result;
      } else {
        value = await response.json();
      }

      if (value.id) {
        setCid(value.id);
      }

      if (value.trace) {
        setCurrentTrace(value.trace);
      }

      setLatestReply(value);
      setPending(null);

      // Add assistant message to chat
      const assistantMsg: ChatMessage = {
        id: crypto.randomUUID(),
        sender: 'assistant',
        text: value.message,
        timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
        trace: value.trace,
        suggested_actions: value.suggested_actions,
        product_offer: value.product_offer,
        can_confirm: value.can_confirm,
        review: value.review,
        confirmation_label: value.confirmation_label,
        country_required: value.country_required,
        input_tool: value.input_tool,
      };

      setMessages((prev) => [...prev, assistantMsg]);

      if (value.input_tool) {
        setTool(value.input_tool);
      } else {
        setTool('');
      }

      // Refresh accounts/cards whenever a banking action completes
      if (value.can_confirm === false && !value.input_tool) {
        refreshBankingData();
      }

      return value;
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Banking request failed');
    } finally {
      setBusy(false);
    }
  }

  function choose(name: string) {
    const read = name.startsWith('view_');
    return read ? request({ path: `/api/conversations/${cid}/inputs`, body: { tool: name, fields: {}, selections: {} }, key: crypto.randomUUID() }) : null;
  }

  function supply(e: React.FormEvent) {
    e.preventDefault();
    return handleSupplyInputs(tool, {});
  }

  function confirm() {
    if (!latestReply?.review || busy || pending) return;
    return handleConfirmAction();
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    return handleSendMessage('');
  }

  function authenticate(e: React.FormEvent) {
    e.preventDefault();
    return handleAuthSubmit(e);
  }

  function logout() {
    if (!session) return;
    return handleSignOut();
  }

  // Chat Actions
  async function handleSendMessage(text: string) {
    if (!text.trim() || busy) return;

    const userMsg: ChatMessage = {
      id: crypto.randomUUID(),
      sender: 'user',
      text,
      timestamp: new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }),
    };

    setMessages((prev) => [...prev, userMsg]);
    addActivity('Chat Inquiry', text.slice(0, 48) + (text.length > 48 ? '...' : ''), 'security');

    if (!cid) {
      const init = await request({
        path: '/api/conversations',
        body: {},
        key: crypto.randomUUID(),
      });
      if (!init?.id) return;
      await request({
        path: `/api/conversations/${init.id}/messages`,
        body: { text },
        key: crypto.randomUUID(),
      });
    } else {
      await request({
        path: `/api/conversations/${cid}/messages`,
        body: { text },
        key: crypto.randomUUID(),
      });
    }
  }

  async function handleConfirmAction() {
    if (!cid || !latestReply?.review || busy) return;
    addActivity('Confirmed Banking Action', latestReply.confirmation_label || 'Confirmed action', 'security');
    await request({
      path: `/api/conversations/${cid}/confirm`,
      body: { review: latestReply.review },
      key: crypto.randomUUID(),
    });
    await refreshBankingData();
  }

  async function handleSelectCountry(countryCode: string) {
    if (!countryCode || !cid || busy) return;
    addActivity('Country Declaration', `Declared location: ${countryCode}`, 'security');
    await request({
      path: `/api/conversations/${cid}/messages`,
      body: { text: countryCode },
      key: crypto.randomUUID(),
    });
  }

  async function handleSupplyInputs(toolName: string, formVals: Record<string, string>) {
    if (!cid || busy) return;
    addActivity('Secure Form Input', `Submitted details for ${toolName}`, 'security');
    const toolDef = tools.find((t) => t.name === toolName);
    const selections: Record<string, string> = {};
    const fields: Record<string, any> = {};

    // Ensure defaults if any required select field is missing from formVals
    const resolvedVals: Record<string, any> = { ...formVals };
    if (toolDef) {
      for (const f of toolDef.fields) {
        if (resolvedVals[f.name] === undefined || resolvedVals[f.name] === '') {
          if (f.name === 'account_type') {
            resolvedVals[f.name] = 'transaction';
          } else if (f.name === 'issue_card' || f.name === 'additional_card' || f.type === 'boolean') {
            resolvedVals[f.name] = true;
          } else if (f.name === 'reason') {
            resolvedVals[f.name] = 'lost';
          } else if (f.options && f.options.length > 0 && f.required) {
            resolvedVals[f.name] = f.options[0].ref;
          }
        }
      }
    }

    for (const [k, v] of Object.entries(resolvedVals)) {
      const fieldDef = toolDef?.fields.find((f) => f.name === k);
      if (fieldDef?.options && fieldDef.options.length > 0) {
        selections[k] = v;
      } else {
        const str = String(v).trim().toLowerCase();
        if (fieldDef?.type === 'boolean' || k === 'issue_card' || k === 'additional_card') {
          fields[k] = str === 'true' || str === 'yes' || str === '1' || v === true;
        } else if (str === 'true') {
          fields[k] = true;
        } else if (str === 'false') {
          fields[k] = false;
        } else {
          fields[k] = v;
        }
      }
    }
    await request({
      path: `/api/conversations/${cid}/inputs`,
      body: { tool: toolName, fields, selections },
      key: crypto.randomUUID(),
    });
    await refreshBankingData();
  }

  async function handleCreateAccount(accountType: 'transaction' | 'savings', issueCard: boolean) {
    if (!session) return;
    const res = await fetch('/api/accounts', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': session.csrf,
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ account_type: accountType, issue_card: issueCard }),
    });

    if (!res.ok) {
      throw new Error('Account creation failed. Please verify your details.');
    }
    addActivity('Account Opened', `Created new ${accountType} account`, 'account');
    await refreshBankingData();
  }

  async function handleIssueCard(accountNumber: string, additionalCard: boolean) {
    if (!session) return;
    const res = await fetch('/api/cards', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': session.csrf,
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ account_number: accountNumber, additional_card: additionalCard }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Card issuance failed. Please review your account.');
    }
    addActivity('Card Issued', `Visa Debit card linked to account •••• ${accountNumber.slice(-4)}`, 'card');
    await refreshBankingData();
  }

  async function handleBlockCard(cardId: string, reason: string) {
    if (!session) return;
    const res = await fetch(`/api/cards/${cardId}/block`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': session.csrf,
        'Idempotency-Key': crypto.randomUUID(),
      },
      body: JSON.stringify({ reason: reason || 'lost' }),
    });

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || 'Could not block card. Please try again.');
    }
    addActivity('Card Blocked', `Blocked card (${reason})`, 'security');
    await refreshBankingData();
  }

  function handleStartChatScenario(scenarioPrompt?: string) {
    setCurrentTab('chat');
    if (scenarioPrompt) {
      setTimeout(() => {
        handleSendMessage(scenarioPrompt);
      }, 100);
    }
  }

  // Authentication Handlers
  async function handleAuthSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    const secret = password;
    setPassword('');
    try {
      const response = await fetch(authAction === 'register' ? '/auth/register' : '/auth/password-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password: secret }),
      });
      const data = await readSessionResponse(response);
      setSession(data);
      setAuthAction('');
      setUsername('');
      setCid('');
      setMessages([]);
      addActivity('Authentication', `Logged in as ${data.name}`, 'auth');
      refreshBankingData();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  }

  async function handleSignOut() {
    if (!session) return;
    try {
      sessionStorage.removeItem(`neobank_cid_${session.name}`);
      sessionStorage.removeItem(`neobank_chat_${session.name}`);
      await fetch('/auth/logout', {
        method: 'POST',
        headers: { 'X-CSRF-Token': session.csrf },
      });
    } catch {
      // Continue client cleanup
    }
    setSession(null);
    setCid('');
    setMessages([]);
    setAccounts([]);
    setCards([]);
    setCurrentTrace(null);
    setCurrentTab('home');
  }

  return (
    <div className="app-shell">
      <Navbar
        currentTab={currentTab}
        onSelectTab={setCurrentTab}
        session={session}
        onSignOut={handleSignOut}
        onOpenAuth={(mode) => {
          setAuthAction(mode);
          setTimeout(() => usernameInputRef.current?.focus(), 50);
        }}
      />

      <main className="main-content">
        {error && (
          <div className="service-alert">
            <span>⚠️</span>
            <span>{error}</span>
            <button
              onClick={() => setError('')}
              style={{ background: 'none', border: 'none', cursor: 'pointer', marginLeft: 'auto', fontWeight: 'bold' }}
            >
              ×
            </button>
          </div>
        )}

        {/* Public Guest Landing */}
        {!session && (
          <div style={{ maxWidth: '480px', margin: '48px auto', textAlign: 'center' }}>
            <div className="surface-card" style={{ padding: '36px 24px' }}>
              <div
                style={{
                  width: '56px',
                  height: '56px',
                  borderRadius: '16px',
                  backgroundColor: '#101828',
                  color: '#2dd4bf',
                  fontSize: '1.8rem',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  margin: '0 auto 16px',
                  fontWeight: 800,
                }}
              >
                N
              </div>
              <h2 style={{ fontSize: '1.6rem', fontWeight: 800, color: '#101828', marginBottom: '8px' }}>
                Welcome to Neo Bank
              </h2>
              <p style={{ color: '#64748b', fontSize: '0.95rem', marginBottom: '24px', lineHeight: 1.5 }}>
                Banking, intelligently connected. Sign in or register to access your verified accounts, Visa Debit cards, and 24/7 banking assistance.
              </p>

              {authAction ? (
                <form onSubmit={handleAuthSubmit} style={{ textAlign: 'left' }}>
                  <div className="form-group">
                    <label htmlFor="auth-username">Username</label>
                    <input
                      ref={usernameInputRef}
                      id="auth-username"
                      type="text"
                      className="form-control"
                      value={username}
                      onChange={(e) => setUsername(e.target.value)}
                      placeholder="e.g. alex.mercer"
                      required
                      autoComplete="username"
                    />
                  </div>

                  <div className="form-group" style={{ marginTop: '12px' }}>
                    <label htmlFor="auth-password">Password</label>
                    <input
                      id="auth-password"
                      type="password"
                      className="form-control"
                      value={password}
                      onChange={(e) => setPassword(e.target.value)}
                      placeholder="At least 10 characters"
                      required
                      minLength={10}
                      autoComplete={authAction === 'register' ? 'new-password' : 'current-password'}
                    />
                  </div>

                  <button
                    type="submit"
                    className="btn-primary"
                    style={{ width: '100%', marginTop: '20px', padding: '12px' }}
                    disabled={busy}
                  >
                    {busy
                      ? 'Please wait...'
                      : authAction === 'register'
                      ? 'Create Neo Account'
                      : 'Sign In to Neo Bank'}
                  </button>

                  <div style={{ textAlign: 'center', marginTop: '14px', fontSize: '0.85rem' }}>
                    <button
                      type="button"
                      style={{ background: 'none', border: 'none', color: '#0d9488', cursor: 'pointer', fontWeight: 600 }}
                      onClick={() => setAuthAction(authAction === 'register' ? 'login' : 'register')}
                    >
                      {authAction === 'register'
                        ? 'Already have an account? Sign in'
                        : "Don't have an account? Register"}
                    </button>
                  </div>
                </form>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>
                  <button
                    className="btn-primary"
                    style={{ width: '100%', padding: '12px', fontSize: '1rem' }}
                    onClick={() => {
                      setAuthAction('login');
                      setTimeout(() => usernameInputRef.current?.focus(), 50);
                    }}
                  >
                    Sign in to your account
                  </button>
                  <button
                    className="btn-secondary"
                    style={{ width: '100%', padding: '12px', fontSize: '1rem' }}
                    onClick={() => {
                      setAuthAction('register');
                      setTimeout(() => usernameInputRef.current?.focus(), 50);
                    }}
                  >
                    Open a new account (Register)
                  </button>
                  <a
                    href="/auth/login"
                    className="btn-secondary"
                    style={{ width: '100%', textAlign: 'center', textDecoration: 'none', marginTop: '4px' }}
                  >
                    Enterprise Single Sign-On
                  </a>
                </div>
              )}
            </div>
          </div>
        )}

        {/* Authenticated Views */}
        {session && (
          <>
            {currentTab === 'home' && (
              <HomeView
                session={session}
                accounts={accounts}
                cards={cards}
                activities={activities}
                onNavigate={setCurrentTab}
                onOpenAccount={() => setCurrentTab('accounts')}
                onIssueCard={() => setCurrentTab('cards')}
              />
            )}

            {currentTab === 'accounts' && (
              <AccountsView
                accounts={accounts}
                isLoading={busy}
                onRefresh={refreshBankingData}
                onCreateAccount={handleCreateAccount}
              />
            )}

            {currentTab === 'cards' && (
              <CardsView
                cards={cards}
                accounts={accounts}
                isLoading={busy}
                onRefresh={refreshBankingData}
                onIssueCard={handleIssueCard}
                onBlockCard={handleBlockCard}
                onNavigateToAccounts={() => setCurrentTab('accounts')}
                onStartChat={handleStartChatScenario}
              />
            )}

            {currentTab === 'chat' && (
              <ChatView
                messages={messages}
                isBusy={busy}
                onSendMessage={handleSendMessage}
                onConfirmAction={handleConfirmAction}
                onCancelAction={() => {
                  setLatestReply(null);
                  handleSendMessage('Cancel that action.');
                }}
                onSelectCountry={handleSelectCountry}
                onActionChipClick={(action) => {
                  if (action === 'block_and_replace') handleSendMessage('Please block and replace my card');
                  else if (action === 'block_card') handleSendMessage('Please block my card');
                  else if (action === 'replace_card') handleSendMessage('Please replace my card');
                  else if (action === 'view_cards') setCurrentTab('cards');
                  else if (action === 'view_accounts') setCurrentTab('accounts');
                }}
                onExploreProducts={() => {
                  addActivity('Product Exploration', 'Customer explored Neo Bank catalogue', 'account');
                  alert('Neo Bank Products:\n• High-Interest Savings Account\n• Multi-Currency Travel Debit Card\n• Smart Spend Budgeting Tools');
                }}
                tools={tools}
                activeTool={tool}
                onSupplyInputs={handleSupplyInputs}
              />
            )}

            {currentTab === 'activity' && (
              <ActivityView activities={activities} onClear={() => setActivities([])} />
            )}
          </>
        )}
      </main>
    </div>
  );
}

// Helper to validate and read session response
async function readSessionResponse(response: Response): Promise<Session> {
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data?.detail || (response.status === 401 ? 'Invalid username or password' : 'Authentication failed'));
  }
  if (typeof data?.name !== 'string' || typeof data?.csrf !== 'string') {
    throw new Error('Malformed session returned by service');
  }
  return { name: data.name, csrf: data.csrf };
}

import { FinCrimeApp } from './fincrime/FinCrimeApp';
import { ErrorBoundary } from './components/ErrorBoundary';

// Root Router supporting Customer Portal (/) and FinCrime Portal (/fincrime)
function RootRouter() {
  const [path, setPath] = useState(window.location.pathname);

  useEffect(() => {
    const handlePopState = () => setPath(window.location.pathname);
    window.addEventListener('popstate', handlePopState);
    return () => window.removeEventListener('popstate', handlePopState);
  }, []);

  if (path.startsWith('/fincrime')) {
    return (
      <ErrorBoundary fallbackTitle="FinCrime Portal Exception">
        <FinCrimeApp />
      </ErrorBoundary>
    );
  }

  return (
    <ErrorBoundary fallbackTitle="Customer Portal Exception">
      <App />
    </ErrorBoundary>
  );
}

createRoot(document.getElementById('root')!).render(
  <ErrorBoundary>
    <RootRouter />
  </ErrorBoundary>
);
