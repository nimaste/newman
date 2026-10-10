// Sign-in and first-run setup, in the dashboard's look.

import { useEffect, useRef, useState } from 'react';
import { api } from '../api.js';
import { Icon } from '../icons.jsx';
import newmanIcon from '../assets/newman-icon-128.png';

/** "Chrome on Windows" — how this browser shows up under "Your devices". */
export function deviceName() {
  const ua = navigator.userAgent || '';
  const browser = /Edg\//.test(ua) ? 'Edge' : /Firefox\//.test(ua) ? 'Firefox' : /Chrome\//.test(ua) ? 'Chrome' : /Safari\//.test(ua) ? 'Safari' : 'Browser';
  const os = /iPad/.test(ua) ? 'iPad' : /iPhone/.test(ua) ? 'iPhone' : /Android/.test(ua) ? 'Android' : /Windows/.test(ua) ? 'Windows' : /Mac OS X/.test(ua) ? 'Mac' : /Linux/.test(ua) ? 'Linux' : '';
  return os ? `${browser} on ${os}` : browser;
}

function WhereLine({ where }) {
  return (
    <div className="auth-where">
      <span className={'dot ' + (where === 'home' ? 'dot--teal' : 'dot--amber')} aria-hidden="true" />
      {where === 'home' ? 'At home' : 'From outside'}
    </div>
  );
}

function Shell({ title, sub, where, children }) {
  return (
    <main className="auth-screen">
      <div className="auth-card">
        <div className="auth-brand">
          <img src={newmanIcon} alt="" width="44" height="44" />
          <span>Newman</span>
        </div>
        <h1 className="auth-title">{title}</h1>
        {sub ? <p className="auth-sub">{sub}</p> : null}
        {children}
        <WhereLine where={where} />
      </div>
    </main>
  );
}

export function SignInPage({ where, onDone }) {
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [remember, setRemember] = useState(true);
  const [step, setStep] = useState('password'); // 'password' | 'totp'
  const [useRecovery, setUseRecovery] = useState(false);
  const [code, setCode] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const codeRef = useRef(null);

  useEffect(() => {
    if (step === 'totp' && codeRef.current) codeRef.current.focus();
  }, [step, useRecovery]);

  const submit = async (e) => {
    e.preventDefault();
    if (busy) return;
    setBusy(true);
    setError('');
    const body = { username: username.trim(), password, remember, kind: 'browser', deviceName: deviceName() };
    if (step === 'totp') {
      if (useRecovery) body.recoveryCode = code.trim();
      else body.code = code.replace(/\s+/g, '');
    }
    try {
      await api.post('api/auth/login', body, { quiet: true });
      await onDone();
    } catch (err) {
      const d = err.data && typeof err.data === 'object' ? err.data : {};
      if (err.status === 401 && d.needs === 'totp') {
        if (step !== 'totp') {
          setStep('totp');
          setCode('');
        }
        if (d.error) setError(d.error);
      } else if (err.status === 0) setError("Can't reach the Newman hub. Check your connection and try again.");
      else setError(err.message || 'Sign-in failed.');
    } finally {
      setBusy(false);
    }
  };

  if (step === 'totp') {
    return (
      <Shell title="Enter your code" sub={useRecovery ? 'Type one of the recovery codes you saved when you turned on two-factor.' : 'Open your authenticator app and type the 6-digit code for Newman.'} where={where}>
        <form className="auth-form" onSubmit={submit}>
          <label className="field">
            <span className="field-label">{useRecovery ? 'Recovery code' : '6-digit code'}</span>
            <input
              ref={codeRef}
              className={'input auth-input' + (useRecovery ? '' : ' auth-code')}
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode={useRecovery ? 'text' : 'numeric'}
              autoComplete="one-time-code"
              maxLength={useRecovery ? 32 : 7}
              placeholder={useRecovery ? 'xxxx-xxxx' : '123456'}
              required
            />
          </label>
          {error ? (
            <div className="auth-error" role="alert">
              <Icon name="alert" size={16} /> {error}
            </div>
          ) : null}
          <button type="submit" className="btn btn-primary auth-submit" disabled={busy || !code.trim()}>
            {busy ? 'Checking…' : 'Sign in'}
          </button>
          <div className="auth-links">
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setUseRecovery((v) => !v);
                setCode('');
                setError('');
              }}
            >
              {useRecovery ? 'Use a code from the app instead' : 'Use a recovery code'}
            </button>
            <button
              type="button"
              className="link-btn"
              onClick={() => {
                setStep('password');
                setCode('');
                setError('');
              }}
            >
              Back
            </button>
          </div>
        </form>
      </Shell>
    );
  }

  return (
    <Shell title="Sign in" where={where}>
      <form className="auth-form" onSubmit={submit}>
        <label className="field">
          <span className="field-label">Username</span>
          <input className="input auth-input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} autoFocus required />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input className="input auth-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required />
        </label>
        <label className="auth-remember">
          <input type="checkbox" checked={remember} onChange={(e) => setRemember(e.target.checked)} />
          Keep me signed in on this device
        </label>
        {error ? (
          <div className="auth-error" role="alert">
            <Icon name="alert" size={16} /> {error}
          </div>
        ) : null}
        <button type="submit" className="btn btn-primary auth-submit" disabled={busy || !username.trim() || !password}>
          {busy ? 'Signing in…' : 'Sign in'}
        </button>
      </form>
    </Shell>
  );
}

export function SetupPage({ where, onDone }) {
  const [name, setName] = useState('');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const suggested = name.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '').slice(0, 32);

  const submit = async (e) => {
    e.preventDefault();
    setError('');
    const user = (username || suggested).trim().toLowerCase();
    if (!/^[a-z0-9._-]{2,32}$/.test(user)) return setError('Usernames are 2–32 letters, digits, dots, dashes or underscores.');
    if (password !== again) return setError("The two passwords don't match.");
    setBusy(true);
    try {
      await api.post('api/auth/setup', { name: name.trim(), username: user, password, deviceName: deviceName() }, { quiet: true });
      await onDone();
    } catch (err) {
      setError(err.status === 0 ? "Can't reach the Newman hub." : err.message);
    } finally {
      setBusy(false);
    }
    return undefined;
  };

  return (
    <Shell title="Create your Newman admin account" sub="This is the first account. You can add the rest of the family afterwards under People → Accounts." where={where}>
      <form className="auth-form" onSubmit={submit}>
        <label className="field">
          <span className="field-label">Your name</span>
          <input className="input auth-input" value={name} onChange={(e) => setName(e.target.value)} autoComplete="name" autoFocus required />
        </label>
        <label className="field">
          <span className="field-label">Username</span>
          <input className="input auth-input" value={username} placeholder={suggested} onChange={(e) => setUsername(e.target.value)} autoComplete="username" autoCapitalize="none" spellCheck={false} />
        </label>
        <label className="field">
          <span className="field-label">Password</span>
          <input className="input auth-input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" required />
        </label>
        <label className="field">
          <span className="field-label">Password again</span>
          <input className="input auth-input" type="password" value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" required />
        </label>
        {error ? (
          <div className="auth-error" role="alert">
            <Icon name="alert" size={16} /> {error}
          </div>
        ) : null}
        <button type="submit" className="btn btn-primary auth-submit" disabled={busy || !name.trim() || !password || !again}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
      </form>
    </Shell>
  );
}
