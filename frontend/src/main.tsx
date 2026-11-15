import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

type Reply = { id?: string; message: string; review: string | null; can_confirm: boolean; confirmation_label: string | null };
type Session = { name: string; csrf: string };
type Job = { status: 'pending' | 'running' | 'succeeded' | 'failed'; status_url: string; result: Reply | null; error: string | null };
type Pending = { path: string; body: object; key: string };
type InputField = {name: string; label: string; type: 'string' | 'integer' | 'number' | 'boolean'; required: boolean; options?: {ref: string; label: string}[]};
type InputTool = {name: string; label: string; fields: InputField[]};

function App() {
  const [session, setSession] = useState<Session | null>(null);
  const [cid, setCid] = useState('');
  const [messages, setMessages] = useState<string[]>([]);
  const [reply, setReply] = useState<Reply | null>(null);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [pending, setPending] = useState<Pending | null>(null);
  const [poll, setPoll] = useState(false);
  const [tools, setTools] = useState<InputTool[]>([]);
  const [tool, setTool] = useState('');
  const [values, setValues] = useState<Record<string,string>>({});
  useEffect(() => { fetch('/api/session').then(r => r.ok ? r.json() : null).then(setSession).catch(() => setError('Session unavailable. Refresh to try again.')); }, []);
  useEffect(() => {
    let active = true;
    setValues({}); setTools([]); setTool('');
    if (cid) fetch(`/api/conversations/${cid}/inputs`).then(r => {
      if (!r.ok) throw new Error('Secure form unavailable. Start a new conversation.');
      return r.json();
    }).then((data: {tools: InputTool[]}) => { if (active) { setTools(data.tools); setTool(data.tools[0]?.name || ''); } })
      .catch(() => { if (active) setError('Secure form unavailable. Start a new conversation.'); });
    return () => { active = false; };
  }, [cid]);

  async function request(action: Pending) {
    setBusy(true); setError(''); setPending(action);
    try {
      const response = await fetch(action.path, { method: 'POST', headers: {
        'Content-Type': 'application/json', 'X-CSRF-Token': session!.csrf,
        'Idempotency-Key': action.key, ...(poll ? { Prefer: 'respond-async' } : {})
      }, body: JSON.stringify(action.body) });
      if (!response.ok) {
        if (response.status >= 400 && response.status < 500) setPending(null);
        if (response.status === 401) setSession(null);
        throw new Error(response.status === 401 ? 'Sign in again to continue.' : response.status < 500 ? 'Details were not accepted. Check the secure form and review again.' : 'Request failed. Retry the same submission.');
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
        if (job.status === 'failed' || !job.result) { setPending(null); throw new Error(job.error || 'Operation failed. Check the form and try again.'); }
        value = job.result;
      } else value = await response.json();
      if (value.id) { setCid(value.id); setMessages([value.message]); }
      else setMessages(old => [...old, value.message]);
      setReply(value); setPending(null);
    } catch (e) { setError(e instanceof Error ? e.message : 'Request interrupted. Retry the same submission.'); }
    finally { setBusy(false); }
  }
  function start() { setReply(null); void request({ path: '/api/conversations', body: {}, key: crypto.randomUUID() }); }
  function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim() || busy || pending) return;
    const content = text; setText(''); setReply(null);
    const privatePaste = /\d(?:[\s-]?\d){2,}|\b(?:cvv|cvc|pin|password|secret|account_number|user_name|given_name)\b|sk-ant-/i.test(content);
    setMessages(old => [...old, privatePaste ? 'You: [Private input omitted. Use the secure action form.]' : `You: ${content}`]);
    void request({ path: `/api/conversations/${cid}/messages`, body: { text: content }, key: crypto.randomUUID() });
  }
  function confirm() {
    if (!reply?.review || busy || pending) return;
    void request({ path: `/api/conversations/${cid}/confirm`, body: { review: reply.review }, key: crypto.randomUUID() });
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
    void request({path: `/api/conversations/${cid}/inputs`, body: {tool, fields, selections}, key: crypto.randomUUID()});
  }
  return <main>
    <header><span className="eyebrow">LOCAL SIMULATION</span><h1>Card servicing</h1><p>Talk through a request. Review the details before you confirm.</p></header>
    {!session ? <a className="button" href="/auth/login">Sign in</a> : <>
      <div className="toolbar"><span>Signed in as {session.name}</span><button disabled={busy} onClick={start}>New conversation</button><button disabled={busy} onClick={async () => {
        const r = await fetch('/auth/logout', {method:'POST', headers:{'X-CSRF-Token':session.csrf}});
        if (r.ok) {setSession(null); setMessages([]); setCid('');}
      }}>Sign out</button></div>
      <label><input type="checkbox" checked={poll} disabled={busy} onChange={e => setPoll(e.target.checked)}/> Submit and poll</label>
      <section aria-live="polite" aria-busy={busy}>{messages.map((message, index) => <p className={message.startsWith('You:') ? 'customer' : 'assistant'} key={index}>{message}</p>)}{busy && <p>Working…</p>}</section>
      {cid && tools.length > 0 && <aside><h2>Secure action form</h2><p>Private details stay with the service. They are not sent to the language model.</p>
        <form onSubmit={supply}><label>Action <select value={tool} disabled={busy || !!pending} onChange={e => {setTool(e.target.value); setValues({});}}>
          {tools.map(item => <option key={item.name} value={item.name}>{item.label}</option>)}
        </select></label>
        {tools.find(item => item.name === tool)?.fields.map(field => <label className="secure-field" key={field.name}>{field.label}
          {field.options ? <select required={field.required} value={values[field.name] || ''} disabled={busy || !!pending} onChange={e => setValues(old => ({...old,[field.name]:e.target.value}))}>
            <option value="">Choose a masked selection</option>{field.options.map(option => <option key={option.ref} value={option.ref}>{option.label}</option>)}
          </select> : field.type === 'boolean' ? <select required={field.required} value={values[field.name] || ''} disabled={busy || !!pending} onChange={e => setValues(old => ({...old,[field.name]:e.target.value}))}><option value="">Choose</option><option value="true">Yes</option><option value="false">No</option></select> :
          <input type={field.type === 'string' ? 'text' : 'number'} step={field.type === 'integer' ? '1' : 'any'} required={field.required} value={values[field.name] || ''} disabled={busy || !!pending} autoComplete="off" maxLength={4000} onChange={e => setValues(old => ({...old,[field.name]:e.target.value}))}/>}
        </label>)}<button disabled={busy || !!pending}>Review action</button></form>
      </aside>}
      {reply?.can_confirm && <aside><strong>Action review</strong><p>{reply.message}</p><button disabled={busy || !!pending} onClick={confirm}>{reply.confirmation_label || 'Confirm action'}</button></aside>}
      {cid && <form onSubmit={submit}><label htmlFor="message">Your message (keep private details in the secure form)</label><textarea id="message" value={text} maxLength={4000} onChange={e => setText(e.target.value)} disabled={busy || !!pending}/><button disabled={busy || !!pending || !text.trim()}>Send message</button></form>}
      {pending && !busy && <button onClick={() => void request(pending)}>Retry same submission</button>}
    </>}
    {error && <p role="alert">{error}</p>}
    <footer>Simulated cards only. Use synthetic customer details.</footer>
  </main>;
}
createRoot(document.getElementById('root')!).render(<App/>);
