// Sign-in settings (admins): what signing in from outside the house requires, and what a wall
// tablet asks before unlocking, opening or disarming.

import { useEffect, useState } from 'react';
import { api, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { Empty, ErrorBox, Loading, Switch } from '../components/Controls.jsx';

const POLICIES = [
  {
    id: 'cert_or_2fa',
    title: 'Device certificate or two-factor',
    tag: 'Recommended',
    text: 'Phones and computers you installed a Newman device certificate (mTLS) on sign in with just the password. Anything else also needs a code from an authenticator app.',
  },
  {
    id: 'cert',
    title: 'Device certificate only',
    text: 'Only devices with a Newman device certificate can sign in from outside. Strongest, but you can’t sign in from a new device while away.',
  },
  {
    id: '2fa',
    title: 'Two-factor for everyone',
    text: 'Everyone needs a code from an authenticator app when signing in from outside. People without two-factor can only sign in at home.',
  },
  {
    id: 'password',
    title: 'Password is enough',
    tag: 'Least protected',
    text: 'A username and password work from anywhere. Anyone who learns a password can get in.',
  },
];

const TABLET = [
  { id: 'none', title: 'Just do it', tag: 'Default', text: 'It’s inside your home; you’ll see and hear it happen.' },
  { id: 'confirm', title: 'Ask “Are you sure?”', text: 'One extra tap, against accidental touches.' },
  { id: 'pin', title: 'Ask for a PIN', text: 'Each person’s PIN from their profile.' },
];

export default function SignInSettings() {
  const { isAdmin, refresh } = useAuth();
  const res = useApi(isAdmin ? 'api/auth/settings' : null);
  const [form, setForm] = useState(null);
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (res.data) setForm({ ...res.data, proxiesText: (res.data.trustedProxies || []).join('\n') });
  }, [res.data]);

  const save = async () => {
    setSaving(true);
    try {
      const body = {
        awayPolicy: form.awayPolicy,
        homeTwoFactor: !!form.homeTwoFactor,
        trustedProxies: form.proxiesText
          .split(/[\s,]+/)
          .map((x) => x.trim())
          .filter(Boolean),
        certHeader: form.certHeader,
        certSubjectHeader: form.certSubjectHeader,
        tabletSensitive: form.tabletSensitive || 'none',
        homeName: (form.homeName || '').trim(),
        outsideUrl: (form.outsideUrl || '').trim(),
      };
      const saved = await api.patch('api/auth/settings', body);
      res.setData(saved);
      refresh();
      toast('Sign-in settings saved', 'success');
    } catch {
      /* toast shown */
    } finally {
      setSaving(false);
    }
  };

  const dirty = form && res.data && (form.awayPolicy !== res.data.awayPolicy || !!form.homeTwoFactor !== !!res.data.homeTwoFactor || form.proxiesText !== (res.data.trustedProxies || []).join('\n') || form.certHeader !== res.data.certHeader || form.certSubjectHeader !== res.data.certSubjectHeader || (form.tabletSensitive || 'none') !== (res.data.tabletSensitive || 'none') || (form.homeName || '') !== (res.data.homeName || '') || (form.outsideUrl || '') !== (res.data.outsideUrl || ''));

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Sign-in settings</h1>
        {isAdmin && form ? (
          <button type="button" className="btn btn-primary head-btn" style={{ marginLeft: 'auto' }} disabled={!dirty || saving} onClick={save}>
            {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
          </button>
        ) : null}
      </div>
      <div className="pane pane--plain">
        <div className="pane-body pane-body--flush-x">
          {!isAdmin ? (
            <div className="card">
              <Empty title="Only an admin can change sign-in settings" />
            </div>
          ) : !form ? (
            res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
          ) : (
            <div className="settings-stack">
              <section className="card prof-card" aria-labelledby="hub-title">
                <h2 id="hub-title" className="prof-title">
                  This Newman
                </h2>
                <p className="help">Phones and wall tablets show this name when they find Newman on the home network, and use the outside address when they're away.</p>
                <label className="field">
                  <span className="field-label">Name</span>
                  <input className="input" maxLength={48} placeholder="Newman" value={form.homeName || ''} onChange={(e) => setForm((f) => ({ ...f, homeName: e.target.value }))} />
                </label>
                <label className="field">
                  <span className="field-label">Outside address</span>
                  <input className="input" inputMode="url" autoCapitalize="none" spellCheck={false} placeholder="https://home.example.com" value={form.outsideUrl || ''} onChange={(e) => setForm((f) => ({ ...f, outsideUrl: e.target.value }))} />
                </label>
              </section>
              <section className="card prof-card" aria-labelledby="away-title">
                <h2 id="away-title" className="prof-title">
                  When signing in from outside the house, require
                </h2>
                <p className="help">At home, a password is enough. From outside, Newman can ask for more. Anyone who has turned on two-factor is always asked for their code when away.</p>
                <div className="policy-list" role="radiogroup" aria-labelledby="away-title">
                  {POLICIES.map((p) => (
                    <button key={p.id} type="button" role="radio" aria-checked={form.awayPolicy === p.id} className="policy-opt" onClick={() => setForm((f) => ({ ...f, awayPolicy: p.id }))}>
                      <span className="joinpick-dot" aria-hidden="true" />
                      <span className="policy-text">
                        <span className="policy-title">
                          {p.title}
                          {p.tag ? <span className={'prof-state' + (p.id === 'password' ? ' is-warn' : ' is-on')}>{p.tag}</span> : null}
                        </span>
                        <span className="policy-desc">{p.text}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <section className="card prof-card" aria-labelledby="home-title">
                <h2 id="home-title" className="prof-title">
                  At home
                </h2>
                <Switch checked={!!form.homeTwoFactor} onChange={(v) => setForm((f) => ({ ...f, homeTwoFactor: v }))} label="Also ask for two-factor at home (for people who turned it on)" />
              </section>
              <section className="card prof-card" aria-labelledby="tablet-title">
                <h2 id="tablet-title" className="prof-title">
                  Wall tablets
                </h2>
                <p className="help">Before unlocking, opening or disarming:</p>
                <div className="policy-list" role="radiogroup" aria-labelledby="tablet-title">
                  {TABLET.map((p) => (
                    <button key={p.id} type="button" role="radio" aria-checked={(form.tabletSensitive || 'none') === p.id} className="policy-opt" onClick={() => setForm((f) => ({ ...f, tabletSensitive: p.id }))}>
                      <span className="joinpick-dot" aria-hidden="true" />
                      <span className="policy-text">
                        <span className="policy-title">
                          {p.title}
                          {p.tag ? <span className="prof-state is-on">{p.tag}</span> : null}
                        </span>
                        <span className="policy-desc">{p.text}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </section>
              <details className="card prof-card settings-adv">
                <summary className="prof-title">Advanced</summary>
                <p className="help">Only needed if Newman sits behind a reverse proxy that checks device certificates. Newman trusts what that proxy says about where a connection comes from and which certificate it showed.</p>
                <label className="field">
                  <span className="field-label">Trusted proxy addresses (one per line, e.g. 172.16.0.0/12)</span>
                  <textarea className="input" rows={3} value={form.proxiesText} onChange={(e) => setForm((f) => ({ ...f, proxiesText: e.target.value }))} />
                </label>
                <label className="field">
                  <span className="field-label">Header with the certificate fingerprint</span>
                  <input className="input" value={form.certHeader || ''} onChange={(e) => setForm((f) => ({ ...f, certHeader: e.target.value }))} />
                </label>
                <label className="field">
                  <span className="field-label">Header with the certificate subject</span>
                  <input className="input" value={form.certSubjectHeader || ''} onChange={(e) => setForm((f) => ({ ...f, certSubjectHeader: e.target.value }))} />
                </label>
              </details>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
