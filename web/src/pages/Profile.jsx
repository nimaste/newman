// Your profile: quiet hours, password, wall-tablet PIN, two-factor, and the devices you're signed in on.

import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import ProfileLook from '../components/ProfileLook.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Check, ErrorBox, Loading, Switch } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import { fmtWhen } from '../format.js';

function Err({ text }) {
  return text ? (
    <div className="auth-error" role="alert">
      <Icon name="alert" size={16} /> {text}
    </div>
  ) : null;
}

function PasswordCard() {
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [again, setAgain] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setError('');
    if (next !== again) return setError("The new passwords don't match.");
    setBusy(true);
    try {
      await api.post('api/auth/password', { current, new: next }, { quiet: true });
      toast('Password changed. Your other devices were signed out.', 'success');
      setCurrent('');
      setNext('');
      setAgain('');
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
    return undefined;
  };
  return (
    <section className="card prof-card" aria-labelledby="pw-title">
      <h2 id="pw-title" className="prof-title">
        Password
      </h2>
      <p className="help">Changing it signs you out everywhere except here.</p>
      <form className="stack-sm" onSubmit={submit}>
        <input className="input" type="password" placeholder="Current password" aria-label="Current password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} required />
        <input className="input" type="password" placeholder="New password" aria-label="New password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} required />
        <input className="input" type="password" placeholder="New password again" aria-label="New password again" autoComplete="new-password" value={again} onChange={(e) => setAgain(e.target.value)} required />
        <Err text={error} />
        <button type="submit" className="btn btn-primary prof-btn" disabled={busy || !current || !next}>
          {busy ? 'Saving…' : 'Change password'}
        </button>
      </form>
    </section>
  );
}

function PinCard({ user, refresh }) {
  const { state } = useAuth();
  const asked = !!state && state.tabletSensitive === 'pin';
  const [pin, setPin] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const save = async (value) => {
    setError('');
    if (value && !/^\d{4,8}$/.test(value)) return setError('A PIN is 4 to 8 digits.');
    setBusy(true);
    try {
      await api.post('api/auth/pin', { password, pin: value }, { quiet: true });
      toast(value ? 'Wall-tablet PIN saved' : 'Wall-tablet PIN removed', 'success');
      setPin('');
      setPassword('');
      refresh();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
    return undefined;
  };
  return (
    <section className="card prof-card" aria-labelledby="pin-title">
      <h2 id="pin-title" className="prof-title">
        PIN for the wall tablet
      </h2>
      <p className="help">
        Only used if an admin turns on “Ask for a PIN” for wall tablets (Sign-in settings); then the tablet asks for it before it unlocks a door, opens the garage or disarms the alarm.{' '}
        {asked ? 'That’s on right now.' : 'Right now the tablets don’t ask for one.'} {user.hasPin ? 'You have one set.' : "You don't have one yet."}
      </p>
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          save(pin);
        }}
      >
        <input className="input" inputMode="numeric" pattern="\d*" maxLength={8} placeholder={user.hasPin ? 'New PIN (4–8 digits)' : 'PIN (4–8 digits)'} aria-label="PIN" autoComplete="off" value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, ''))} />
        <input className="input" type="password" placeholder="Your password" aria-label="Your password (for the PIN)" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        <Err text={error} />
        <div className="inline-form">
          <button type="submit" className="btn btn-primary prof-btn" disabled={busy || !pin || !password}>
            {user.hasPin ? 'Change PIN' : 'Set PIN'}
          </button>
          {user.hasPin ? (
            <button type="button" className="btn prof-btn" disabled={busy || !password} onClick={() => save('')}>
              Remove PIN
            </button>
          ) : null}
        </div>
      </form>
    </section>
  );
}

function download(name, text) {
  const blob = new Blob([text], { type: 'text/plain' });
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

function TwoFactorCard({ user, refresh }) {
  const [step, setStep] = useState('idle'); // idle | password | scan | codes | off
  const [password, setPassword] = useState('');
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState('');
  const [codes, setCodes] = useState(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const reset = () => {
    setStep('idle');
    setPassword('');
    setSetup(null);
    setCode('');
    setError('');
  };
  const run = async (fn) => {
    setBusy(true);
    setError('');
    try {
      await fn();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };

  let body;
  if (step === 'codes' && codes) {
    const text = codes.join('\n');
    body = (
      <div className="stack-sm">
        <p className="help">
          <b>Save these recovery codes now.</b> Each one signs you in once if you lose your phone. They won't be shown again.
        </p>
        <ol className="recovery-codes">
          {codes.map((c) => (
            <li key={c}>{c}</li>
          ))}
        </ol>
        <div className="inline-form">
          <button type="button" className="btn prof-btn" onClick={() => navigator.clipboard && navigator.clipboard.writeText(text).then(() => toast('Copied', 'success'), () => toast("Couldn't copy — select them instead", 'error'))}>
            <Icon name="copy" size={15} /> Copy
          </button>
          <button type="button" className="btn prof-btn" onClick={() => download('newman-recovery-codes.txt', `Newman recovery codes for ${user.username}\n\n${text}\n`)}>
            <Icon name="download" size={15} /> Download
          </button>
          <button
            type="button"
            className="btn btn-primary prof-btn"
            onClick={() => {
              setCodes(null);
              reset();
            }}
          >
            I've saved these
          </button>
        </div>
      </div>
    );
  } else if (step === 'scan' && setup) {
    body = (
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            const res = await api.post('api/auth/2fa/enable', { code: code.replace(/\s+/g, '') }, { quiet: true });
            setCodes(res.recoveryCodes || []);
            setStep('codes');
            refresh();
          });
        }}
      >
        <p className="help">Scan this with an authenticator app (Google Authenticator, 1Password, Authy…), then type the code it shows.</p>
        {/* The QR code is an SVG the hub generates from your secret. */}
        <div className="qr-box" dangerouslySetInnerHTML={{ __html: setup.qrSvg }} />
        <div className="help">
          Can't scan? Enter this key instead: <code className="secret">{setup.secret}</code>
        </div>
        <input className="input" inputMode="numeric" autoComplete="one-time-code" maxLength={7} placeholder="6-digit code" aria-label="Code from your authenticator app" value={code} onChange={(e) => setCode(e.target.value)} autoFocus />
        <Err text={error} />
        <div className="inline-form">
          <button type="submit" className="btn btn-primary prof-btn" disabled={busy || code.replace(/\s+/g, '').length < 6}>
            Turn on
          </button>
          <button type="button" className="btn prof-btn" onClick={reset}>
            Cancel
          </button>
        </div>
      </form>
    );
  } else if (step === 'password' || step === 'off') {
    const turningOff = step === 'off';
    body = (
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          run(async () => {
            if (turningOff) {
              await api.post('api/auth/2fa/disable', { password }, { quiet: true });
              toast('Two-factor is off', 'success');
              reset();
              refresh();
            } else {
              const res = await api.post('api/auth/2fa/start', { password }, { quiet: true });
              setSetup(res);
              setPassword('');
              setStep('scan');
            }
          });
        }}
      >
        <input className="input" type="password" placeholder="Your password" aria-label="Your password (for two-factor)" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
        <Err text={error} />
        <div className="inline-form">
          <button type="submit" className={'btn prof-btn' + (turningOff ? ' btn-danger' : ' btn-primary')} disabled={busy || !password}>
            {turningOff ? 'Turn off two-factor' : 'Continue'}
          </button>
          <button type="button" className="btn prof-btn" onClick={reset}>
            Cancel
          </button>
        </div>
      </form>
    );
  } else {
    body = (
      <div className="inline-form">
        {user.twoFactor ? (
          <button type="button" className="btn prof-btn" onClick={() => setStep('off')}>
            Turn off
          </button>
        ) : (
          <button type="button" className="btn btn-primary prof-btn" onClick={() => setStep('password')}>
            Turn on
          </button>
        )}
      </div>
    );
  }

  return (
    <section className="card prof-card" aria-labelledby="tfa-title">
      <h2 id="tfa-title" className="prof-title">
        Two-factor sign-in <span className={'prof-state' + (user.twoFactor ? ' is-on' : '')}>{user.twoFactor ? 'On' : 'Off'}</span>
      </h2>
      <p className="help">Besides your password, Newman asks for a code from an app on your phone — depending on the sign-in settings, when you sign in from outside the house.</p>
      {body}
    </section>
  );
}

function DevicesCard() {
  const confirm = useConfirm();
  const { signOut } = useAuth();
  const sessions = useApi('api/auth/sessions');
  const list = (sessions.data || []).slice().sort((a, b) => (b.current ? 1 : 0) - (a.current ? 1 : 0) || (b.lastSeen || 0) - (a.lastSeen || 0));
  const revoke = async (s) => {
    if (s.current) {
      if (await confirm({ title: 'Sign out of this device?', confirmLabel: 'Sign out' })) signOut();
      return;
    }
    if (!(await confirm({ title: `Sign out ${s.deviceName || 'that device'}?`, body: 'It will have to sign in again.', confirmLabel: 'Sign out', danger: true }))) return;
    try {
      await api.del('api/auth/sessions/' + enc(s.id));
      toast(`Signed out ${s.deviceName || 'that device'}`, 'success');
    } catch {
      /* toast shown */
    }
    sessions.reload();
  };
  const kindLabel = { browser: 'Browser', app: 'Newman app', tablet: 'Wall tablet' };
  // Which dashboard a tablet (or app) shows: one of yours, or your default.
  const dashboards = useApi('api/dashboards');
  const setShows = async (s, dashboardId) => {
    try {
      await api.patch('api/auth/sessions/' + enc(s.id), { dashboardId });
      toast(`${s.deviceName || 'The device'} now shows ${dashboardId ? (dashboards.data || []).find((d) => d.id === dashboardId)?.name || 'that dashboard' : 'your default'}`, 'success');
    } catch {
      /* toast shown */
    }
    sessions.reload();
  };
  return (
    <section className="card prof-card prof-card--wide" aria-labelledby="dev-title">
      <h2 id="dev-title" className="prof-title">
        Your devices
      </h2>
      {sessions.data === undefined ? (
        sessions.error ? <ErrorBox error={sessions.error} onRetry={sessions.reload} /> : <Loading />
      ) : (
        <div className="rows">
          {list.map((s) => (
            <div key={s.id} className="row session-row">
              <span className="phone-icon" aria-hidden="true">
                <Icon name={s.kind === 'browser' ? 'grid' : 'phone'} size={16} />
              </span>
              <div className="grow">
                <div className="strong">
                  {s.deviceName || kindLabel[s.kind] || 'Device'}
                  {s.current ? <span className="prof-state is-on">This device</span> : null}
                </div>
                <div className="phone-meta">
                  {kindLabel[s.kind] || s.kind} · last seen {fmtWhen(s.lastSeen)} · {s.where === 'home' ? 'at home' : 'from outside'}
                  {s.certificate ? ' · device certificate' : ''}
                  {s.remember ? '' : ' · until the browser closes'}
                </div>
              </div>
              {s.kind !== 'browser' ? (
                <label className="inline-form">
                  <span className="muted">Shows</span>
                  <select className="select select--sm" value={s.dashboardId || ''} onChange={(ev) => setShows(s, ev.target.value || null)} aria-label={`What ${s.deviceName || 'this device'} shows`}>
                    <option value="">My default</option>
                    {(dashboards.data || []).map((d) => (
                      <option key={d.id} value={d.id}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button type="button" className="btn btn-sm2" onClick={() => revoke(s)}>
                Sign out
              </button>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

/** Your colour and photo on the family maps (household members only). */
function LookCard({ user }) {
  const people = useApi(user.personId ? 'api/people' : null);
  const me = (people.data || []).find((p) => p.id === user.personId);
  if (!me) return null;
  return (
    <section className="card prof-card prof-card--wide" aria-label="On the family map">
      <ProfileLook person={me} mine onChanged={people.reload} />
    </section>
  );
}

const QUIET_ALLOW = [
  { id: 'alarm', label: 'The alarm, leaks and smoke' },
  { id: 'doorbell', label: 'The doorbell' },
  { id: 'people', label: 'People on cameras' },
  { id: 'urgent', label: 'Automations marked urgent' },
];

/** Quiet hours (GET/PUT api/me/quiet-hours): their phones get everyday notifications silently in that window. */
function QuietHoursCard() {
  const res = useApi('api/me/quiet-hours');
  const q = res.data;
  const put = async (change) => {
    const next = { ...q, ...change };
    res.setData(next);
    try {
      res.setData(await api.put('api/me/quiet-hours', next));
    } catch {
      res.reload();
    }
  };
  return (
    <section className="card prof-card prof-card--wide" aria-labelledby="quiet-title">
      {!q ? (
        res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
      ) : (
        <div className="set-list">
          <div className="set-row">
            <div className="set-text">
              <h2 id="quiet-title" className="set-name quiet-title">
                Quiet hours
              </h2>
              <span className="set-help">Your phone stays silent for everyday notifications. They still arrive, without a sound.</span>
            </div>
            <div className="set-control set-control--end">
              <Switch checked={!!q.enabled} ariaLabel="Quiet hours" onChange={(v) => put({ enabled: v })} />
            </div>
          </div>
          <div className="set-row">
            <div className="set-text">
              <span className="set-name set-name--sm">When</span>
            </div>
            <div className="set-control">
              <span className="set-inline">
                <label className="set-word" htmlFor="quiet-from">
                  From
                </label>
                <input id="quiet-from" type="time" className="input input--sm quiet-time" defaultValue={q.from} key={'f' + q.from} onBlur={(e) => e.target.value && e.target.value !== q.from && put({ from: e.target.value })} />
                <label className="set-word" htmlFor="quiet-to">
                  to
                </label>
                <input id="quiet-to" type="time" className="input input--sm quiet-time" defaultValue={q.to} key={'t' + q.to} onBlur={(e) => e.target.value && e.target.value !== q.to && put({ to: e.target.value })} />
                <select className="input input--sm" aria-label="Days" value={q.days} onChange={(e) => put({ days: e.target.value })}>
                  <option value="all">Every day</option>
                  <option value="weekdays">Weekdays</option>
                  <option value="weekends">Weekends</option>
                </select>
              </span>
            </div>
          </div>
          <div className="set-row">
            <div className="set-text">
              <span className="set-name set-name--sm">Still ring through</span>
            </div>
            <div className="set-control">
              <span className="radio-stack">
                {QUIET_ALLOW.map((a) => (
                  <Check key={a.id} checked={(q.allow || []).includes(a.id)} onChange={(v) => put({ allow: v ? [...(q.allow || []), a.id] : (q.allow || []).filter((x) => x !== a.id) })}>
                    {a.label}
                  </Check>
                ))}
              </span>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}

export default function Profile() {
  const { user, refresh } = useAuth();
  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Your profile</h1>
      </div>
      <div className="pane pane--plain">
        <div className="pane-body pane-body--flush-x">
          <div className="prof-grid">
            <LookCard user={user} />
            {user.personId ? <QuietHoursCard /> : null}
            <PasswordCard />
            <PinCard user={user} refresh={refresh} />
            <TwoFactorCard user={user} refresh={refresh} />
            <DevicesCard />
          </div>
        </div>
      </div>
    </div>
  );
}
