import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';
import { readSessionResponse } from './http';

type Reply = {
  id?: string;
  message: string;
  review: string | null;
  can_confirm: boolean;
  confirmation_label: string | null;
  input_tool?: string | null;
  country_required?: boolean;
  data?: {
    accounts?: { account_number: string; account_type: string }[];
    cards?: { id: string; account_number: string; card_number: string; status?: string }[];
  };
};

type Session = { name: string; csrf: string };
type Job = { status: 'pending' | 'running' | 'succeeded' | 'failed'; status_url: string; result: Reply | null; error: string | null };
type Pending = { path: string; body: object; key: string };
type InputField = { name: string; label: string; type: 'string' | 'integer' | 'number' | 'boolean'; required: boolean; options?: { ref: string; label: string }[] };
type InputTool = { name: string; label: string; fields: InputField[] };

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [cid, setCid] = useState('');
  const [messages, setMessages] = useState<string[]>([]);
  const [reply, setReply] = useState<Reply | null>(null);
  const [text, setText] = useState('');
  const [country, setCountry] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [poll, setPoll] = useState(false);
  const [tools, setTools] = useState<InputTool[]>([]);
  const [tool, setTool] = useState('');
  const [values, setValues] = useState<Record<string, string>>({});
  const [showInputs, setShowInputs] = useState(false);
  const [desiredTool, setDesiredTool] = useState('');
  const [authAction, setAuthAction] = useState<'register' | 'login' | 'logout' | ''>('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  // Auto-scroll anchor refs
  const authRef = useRef<HTMLElement | null>(null);
  const inputsRef = useRef<HTMLElement | null>(null);
  const reviewRef = useRef<HTMLElement | null>(null);
  const dataRef = useRef<HTMLElement | null>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const usernameInputRef = useRef<HTMLInputElement | null>(null);
  const countryInputRef = useRef<HTMLInputElement | null>(null);

  // Standard scroll-into-view helper
  const scrollTo = (el: HTMLElement | null) => {
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }
  };

  // Reusable Auto-Scroll Pattern for all user actions & transitions
  useEffect(() => {
    if (authAction && authRef.current) {
      scrollTo(authRef.current);
      if (['register', 'login'].includes(authAction)) {
        setTimeout(() => usernameInputRef.current?.focus(), 100);
      }
    }
  }, [authAction]);

  useEffect(() => {
    if (showInputs && inputsRef.current) {
      scrollTo(inputsRef.current);
    }
  }, [showInputs, tool]);

  useEffect(() => {
    if (reply?.country_required && !busy) {
      scrollTo(countryInputRef.current);
      countryInputRef.current?.focus();
    }
  }, [reply?.country_required, busy]);

  useEffect(() => {
    if (reply?.can_confirm && reviewRef.current) {
      scrollTo(reviewRef.current);
    } else if ((reply?.data?.cards || reply?.data?.accounts) && dataRef.current) {
      scrollTo(dataRef.current);
    } else if (chatEndRef.current && messages.length > 0) {
      scrollTo(chatEndRef.current);
    }
  }, [reply, messages]);

  useEffect(() => {
    fetch('/api/session')
      .then(r => (r.ok ? readSessionResponse(r) : null))
      .then(setSession)
      .catch(() => setError('Session unavailable. Refresh to try again.'));
  }, []);

  useEffect(() => {
    let active = true;
    setValues({});
    setTools([]);
    setCountry('');
    setTool('');
    setShowInputs(false);
    if (cid) {
      fetch(`/api/conversations/${cid}/inputs`)
        .then(r => {
          if (!r.ok) throw new Error('Secure form unavailable. Start a new conversation.');
          return r.json();
        })
        .then((data: { tools: InputTool[] }) => {
          if (active) {
            setTools(data.tools);
            if (desiredTool) {
              setTool(desiredTool);
              setShowInputs(true);
            }
          }
        })
        .catch(() => {
          if (active) setError('Secure form unavailable. Start a new conversation.');
        });
    }
    return () => {
      active = false;
    };
  }, [cid]);

  async function request(action: Pending) {
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
          ...(poll ? { Prefer: 'respond-async' } : {})
        },
        body: JSON.stringify(action.body)
      });
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500) setPending(null);
        if (response.status === 401) setSession(null);
        throw new Error(
          response.status === 401
            ? 'Sign in again to continue.'
            : response.status < 500
            ? 'Details were not accepted. Check the secure form and review again.'
            : 'Request failed. Retry the same submission.'
        );
      }
      let value: Reply;
      if (response.status === 202) {
        let job: Job = await response.json();
        while (job.status === 'pending' || job.status === 'running') {
          await new Promise(resolve => setTimeout(resolve, 500));
          const status = await fetch(job.status_url);
          if (!status.ok) throw new Error('Polling interrupted. Retry the same submission.');
          job = await status.json();
        }
        if (job.status === 'failed' || !job.result) {
          setPending(null);
          throw new Error(job.error || 'Operation failed. Check the form and try again.');
        }
        value = job.result;
      } else {
        value = await response.json();
      }

      // A banking action can change which cards are eligible without changing
      // the conversation ID. Refresh signed, server-generated selections before
      // displaying the next form (especially block -> country -> replacement).
      if (session && cid && !value.id) {
        const options = await fetch(`/api/conversations/${cid}/inputs`, { cache: 'no-store' });
        if (!options.ok) {
          setTools([]);
          setShowInputs(false);
          throw new Error('Card choices could not be refreshed. Retry the same submission.');
        }
        const data: { tools: InputTool[] } = await options.json();
        setTools(data.tools);
      }

      if (value.id) {
        setCid(value.id);
        setMessages([value.message]);
      } else {
        setMessages(old => [...old, value.message]);
      }

      if (value.input_tool) {
        if (['register', 'login', 'logout'].includes(value.input_tool)) {
          setAuthAction(value.input_tool as 'register' | 'login' | 'logout');
        }
        if (tool !== value.input_tool) setValues({});
        setTool(value.input_tool);
        setShowInputs(true);
      } else {
        setShowInputs(false);
      }
      setReply(value);
      setPending(null);
      return value;
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Request interrupted. Retry the same submission.');
    } finally {
      setBusy(false);
    }
  }

  function start() {
    setDesiredTool('');
    setReply(null);
    void request({ path: '/api/conversations', body: {}, key: crypto.randomUUID() });
  }

  async function choose(name: string) {
    const read = name.startsWith('view_');
    setDesiredTool(read ? '' : name);
    setReply(null);
    const value = await request({ path: '/api/conversations', body: {}, key: crypto.randomUUID() });
    if (read && value?.id) {
      await request({ path: `/api/conversations/${value.id}/inputs`, body: { tool: name, fields: {} }, key: crypto.randomUUID() });
    }
  }

  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy || pending) return;
    const content = text;
    setText('');
    setReply(null);
    const privatePaste = /\d(?:[\s-]?\d){2,}|\b(?:cvv|cvc|pin|password|secret|account_number|user_name|given_name)\b|sk-ant-/i.test(content);
    setMessages(old => [...old, privatePaste ? 'You: [Private input omitted. Use the secure action form.]' : `You: ${content}`]);
    void request({
      path: session ? `/api/conversations/${cid}/messages` : '/api/entry/messages',
      body: { text: content },
      key: crypto.randomUUID()
    });
  }

  function confirm() {
    if (!reply?.review || busy || pending) return;
    void request({ path: `/api/conversations/${cid}/confirm`, body: { review: reply.review }, key: crypto.randomUUID() });
  }

  function supplyCountry(e: React.FormEvent) {
    e.preventDefault();
    if (!country.trim() || busy || pending || !cid) return;
    void request({ path: `/api/conversations/${cid}/messages`,
      body: { text: country.trim() }, key: crypto.randomUUID() });
  }

  function supply(e: React.FormEvent) {
    e.preventDefault();
    if (busy || pending) return;
    const action = tools.find(item => item.name === tool);
    if (!action) return;
    const fields: Record<string, string | number | boolean> = {};
    const selections: Record<string, string> = {};
    for (const field of action.fields) {
      const value = values[field.name];
      if (value === undefined || value === '') continue;
      if (field.options) selections[field.name] = value;
      else fields[field.name] = field.type === 'boolean' ? value === 'true' : field.type === 'string' ? value : Number(value);
    }
    setReply(null);
    void request({ path: `/api/conversations/${cid}/inputs`, body: { tool, fields, selections }, key: crypto.randomUUID() });
  }

  async function authenticate(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError('');
    const secret = password;
    setPassword('');
    try {
      const response = await fetch(authAction === 'register' ? '/auth/register' : '/auth/password-login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password: secret })
      });
      const data = await readSessionResponse(response);
      setSession(data);
      setAuthAction('');
      setUsername('');
      setCid('');
      setReply(null);
      setPending(null);
      setMessages(['Welcome. Start a conversation to view accounts, create an account or issue a card.']);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Sign-in unavailable');
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    if (!session) return;
    const r = await fetch('/auth/logout', { method: 'POST', headers: { 'X-CSRF-Token': session.csrf } });
    if (r.ok) {
      setSession(null);
      setMessages([]);
      setCid('');
      setReply(null);
      setPending(null);
      setAuthAction('');
      setValues({});
    } else {
      setError('Sign out failed. Try again.');
    }
  }

  return (
    <main>
      <header>
        <span className="eyebrow">LOCAL SIMULATION</span>
        <h1>Card servicing</h1>
        <p>Talk through a request. Review the details before you confirm.</p>
      </header>

      {!session ? (
        <>
          <div className="toolbar">
            <button disabled={busy} onClick={() => setAuthAction('register')}>
              Register / Sign up
            </button>
            <button disabled={busy} onClick={() => setAuthAction('login')}>
              Login
            </button>
            <a href="/auth/login" className="button">
              OIDC sign in
            </a>
          </div>

          {['register', 'login'].includes(authAction) && (
            <aside ref={authRef} className="auth-card" tabIndex={-1}>
              <h2>{authAction === 'register' ? 'Register' : 'Login'}</h2>
              <form onSubmit={authenticate}>
                <label className="secure-field">
                  Username
                  <input
                    ref={usernameInputRef}
                    value={username}
                    onChange={e => setUsername(e.target.value)}
                    autoComplete="username"
                    pattern="[A-Za-z][A-Za-z0-9_.-]{2,63}"
                    required
                    maxLength={64}
                  />
                </label>
                <label className="secure-field">
                  Password
                  <input
                    type="password"
                    value={password}
                    onChange={e => setPassword(e.target.value)}
                    autoComplete={authAction === 'register' ? 'new-password' : 'current-password'}
                    minLength={10}
                    maxLength={128}
                    required
                  />
                </label>
                <p>Use 3–64 characters for your username and at least 10 characters for your password. Credentials stay out of chat.</p>
                <div className="action-buttons">
                  <button disabled={busy}>{authAction === 'register' ? 'Create customer and sign in' : 'Login'}</button>
                  <button type="button" className="button-secondary" disabled={busy} onClick={() => setAuthAction('')}>
                    Cancel
                  </button>
                </div>
              </form>
            </aside>
          )}

          <section aria-live="polite">
            {messages.map((m, i) => (
              <p key={i}>{m}</p>
            ))}
            <div ref={chatEndRef} />
          </section>

          <form onSubmit={submit}>
            <label>
              How can we help?
              <textarea value={text} onChange={e => setText(e.target.value)} placeholder="I want to register" maxLength={4000} />
            </label>
            <p>Keep credentials out of chat. Use the secure form.</p>
            <button disabled={busy || !!pending || !text.trim()}>Send message</button>
          </form>
        </>
      ) : (
        <>
          <div className="toolbar">
            <span>Signed in as {session.name}</span>
            <button disabled={busy} onClick={start}>
              {cid ? 'New conversation' : 'Start conversation'}
            </button>
            <button disabled={busy} onClick={() => setAuthAction('logout')}>
              Sign out
            </button>
          </div>

          {authAction === 'logout' && (
            <aside ref={authRef} className="auth-card" tabIndex={-1}>
              <h2>Sign out</h2>
              <p>Sign out of this session?</p>
              <div className="action-buttons">
                <button disabled={busy} onClick={() => void logout()}>
                  Confirm sign out
                </button>
                <button type="button" className="button-secondary" disabled={busy} onClick={() => setAuthAction('')}>
                  Cancel
                </button>
              </div>
            </aside>
          )}

          <nav aria-label="Banking actions" className="toolbar">
            {[
              ['view_accounts', 'View my accounts'],
              ['create_account', 'Create account'],
              ['view_cards', 'View my cards'],
              ['issue_card', 'Issue new card'],
              ['replace_card', 'Replace card'],
              ['block_card', 'Block card'],
              ['unblock_card', 'Unblock card']
            ].map(([name, label]) => (
              <button key={name} disabled={busy || !!pending} onClick={() => choose(name)}>
                {label}
              </button>
            ))}
          </nav>

          <label>
            <input type="checkbox" checked={poll} disabled={busy} onChange={e => setPoll(e.target.checked)} /> Submit and poll
          </label>

          <section aria-live="polite" aria-busy={busy}>
            {messages.map((message, index) => (
              <p className={message.startsWith('You:') ? 'customer' : 'assistant'} key={index}>
                {message}
              </p>
            ))}
            {busy && <p>Working…</p>}
            <div ref={chatEndRef} />
          </section>

          {cid && (
            <form onSubmit={submit}>
              <label htmlFor="message">How can we help?</label>
              <textarea
                id="message"
                placeholder="For example: I would like a new card."
                value={text}
                maxLength={4000}
                onChange={e => setText(e.target.value)}
                disabled={busy || !!pending}
              />
              <p>Describe what you need. Keep account numbers and other private details out of chat.</p>
              <button disabled={busy || !!pending || !text.trim()}>Send message</button>
            </form>
          )}

          {cid && reply?.country_required && (
            <aside aria-label="Current country">
              <h2>Which country are you currently in?</h2>
              <form onSubmit={supplyCountry}>
                <label htmlFor="current-country">Current country</label>
                <input id="current-country" ref={countryInputRef} value={country} onChange={e => setCountry(e.target.value)}
                  placeholder="For example: Australia" autoComplete="country-name" maxLength={100}
                  required disabled={busy || !!pending} />
                <p>We’ll use your declared country to check replacement availability. You’ll still review and confirm any eligible replacement.</p>
                <button disabled={busy || !!pending || !country.trim()}>Continue with country</button>
              </form>
            </aside>
          )}

          {cid && showInputs && tools.some(item => item.name === tool) && (
            <aside ref={inputsRef} tabIndex={-1}>
              <h2>{tools.find(item => item.name === tool)?.label}: private details</h2>
              <p>Private details stay with the service. They are not sent to the language model.</p>
              {['issue_card', 'create_card'].includes(tool) && (
                <p>Ordinary requests return your existing card. Select “Issue an additional card” only if you want another card, then review and confirm.</p>
              )}
              {tool === 'issue_card' && tools.find(t => t.name === tool)?.fields.some(f => f.options?.length === 0) && (
                <p>
                  Create an account first.{' '}
                  <button
                    onClick={() => {
                      setTool('create_account');
                      setValues({});
                    }}
                  >
                    Create account
                  </button>
                </p>
              )}
              {tool === 'unblock_card' && tools.find(t => t.name === tool)?.fields.some(f => f.name === 'card_id' && f.options?.length === 0) && (
                <p>No blocked cards are currently available to unblock.</p>
              )}
              {tool === 'replace_card' && tools.find(t => t.name === tool)?.fields.some(f => f.name === 'card_id' && f.options?.length === 0) && (
                <p>No blocked cards are currently available to replace.</p>
              )}
              {tool === 'block_card' && tools.find(t => t.name === tool)?.fields.some(f => f.name === 'card_id' && f.options?.length === 0) && (
                <p>No active cards are available to block.</p>
              )}
              <form onSubmit={supply}>
                {tools
                  .find(item => item.name === tool)
                  ?.fields.map(field => (
                    <label className="secure-field" key={field.name}>
                      {field.label}
                      {field.options ? (
                        <select
                          required={field.required}
                          value={values[field.name] || ''}
                          disabled={busy || !!pending}
                          onChange={e => setValues(old => ({ ...old, [field.name]: e.target.value }))}
                        >
                          <option value="">Choose a masked selection</option>
                          {field.options.map(option => (
                            <option key={option.ref} value={option.ref}>
                              {option.label}
                            </option>
                          ))}
                        </select>
                      ) : field.type === 'boolean' ? (
                        <select
                          required={field.required}
                          value={values[field.name] || ''}
                          disabled={busy || !!pending}
                          onChange={e => setValues(old => ({ ...old, [field.name]: e.target.value }))}
                        >
                          <option value="">Choose</option>
                          <option value="true">Yes</option>
                          <option value="false">No</option>
                        </select>
                      ) : (
                        <input
                          type={field.type === 'string' ? 'text' : 'number'}
                          step={field.type === 'integer' ? '1' : 'any'}
                          required={field.required}
                          value={values[field.name] || ''}
                          disabled={busy || !!pending}
                          autoComplete="off"
                          maxLength={4000}
                          onChange={e => setValues(old => ({ ...old, [field.name]: e.target.value }))}
                        />
                      )}
                    </label>
                  ))}
                <button disabled={busy || !!pending}>Review action</button>
              </form>
            </aside>
          )}

          {reply?.can_confirm && (
            <aside ref={reviewRef} tabIndex={-1}>
              <strong>Action review</strong>
              <p>{reply.message}</p>
              <button disabled={busy || !!pending} onClick={confirm}>
                {reply.confirmation_label || 'Confirm action'}
              </button>
              {tool && (
                <button disabled={busy || !!pending} onClick={() => setShowInputs(true)}>
                  Edit private details
                </button>
              )}
            </aside>
          )}

          {reply?.data?.accounts && (
            <aside ref={dataRef} tabIndex={-1}>
              <h2>My accounts</h2>
              {reply.data.accounts.map(a => (
                <p key={a.account_number}>
                  {a.account_type} · {a.account_number}
                </p>
              ))}
              {!reply.data.accounts.length && <button onClick={() => choose('create_account')}>Create account</button>}
            </aside>
          )}

          {reply?.data?.cards && (
            <aside ref={dataRef} tabIndex={-1}>
              <h2>My cards</h2>
              {reply.data.cards.map(c => (
                <p key={c.id}>
                  {c.card_number} · Account ending {c.account_number.slice(-4)}
                  {c.status ? ` · [${c.status.toUpperCase()}]` : ''}
                </p>
              ))}
            </aside>
          )}

          {cid && tools.length > 0 && (
            <details>
              <summary>Choose an action manually</summary>
              <p>You can also describe your request in chat above.</p>
              <select
                aria-label="Choose an action manually"
                value={tool}
                disabled={busy || !!pending}
                onChange={e => {
                  setTool(e.target.value);
                  setValues({});
                  setShowInputs(!!e.target.value);
                }}
              >
                <option value="">Choose an action</option>
                {tools.map(item => (
                  <option key={item.name} value={item.name}>
                    {item.label}
                  </option>
                ))}
              </select>
              <button disabled={busy || !!pending || !tool} onClick={() => setShowInputs(true)}>
                Enter private details
              </button>
            </details>
          )}

          {pending && !busy && <button onClick={() => void request(pending)}>Retry same submission</button>}
        </>
      )}

      {error && <p role="alert">{error}</p>}
      <footer>Simulated cards only. Use synthetic customer details.</footer>
    </main>
  );
}

createRoot(document.getElementById('root')!).render(<App />);
