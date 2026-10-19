// Extras on a connection's page, driven by what it reports / which fields it has (never its type):
// webhook addresses (from the admin-only dminDetails.addresses: each one is a key) and
// "Add a device" by pairing code, and signing in to Amazon (adminDetails.amazon) on Amazon's own page.

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { Check, Empty, Modal } from './Controls.jsx';
import { useConfirm } from './Feedback.jsx';
import { Icon } from '../icons.jsx';
import { cleanDetail, fmtAgo } from '../format.js';

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    // Older browsers / plain http: a hidden textarea.
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    let ok = false;
    try {
      ok = document.execCommand('copy');
    } catch {
      ok = false;
    }
    ta.remove();
    return ok;
  }
}

/** Each webhook's incoming address ({name, entityId, path} → origin + path), hidden until shown, with Copy. */
export function WebhookAddresses({ hooks }) {
  const [shown, setShown] = useState({});
  const origin = window.location.origin;
  return (
    <section className="hook-box" aria-label="Webhook addresses">
      <div className="hook-head">
        <Icon name="webhook" size={16} /> Incoming addresses
        <span className="muted small">Anyone with an address can trigger it: share it only with the service that calls it.</span>
      </div>
      {hooks.map((h) => {
        const url = origin + h.path;
        const open = !!shown[h.path];
        return (
          <div key={h.path} className="hook-row">
            <span className="hook-name">{h.name}</span>
            <code className="hook-url" title={open ? url : undefined}>
              {open ? url : origin + cleanDetail(h.path)}
            </code>
            <button type="button" className="btn btn-sm2" aria-pressed={open} onClick={() => setShown((s) => ({ ...s, [h.path]: !open }))}>
              {open ? 'Hide' : 'Show'}
            </button>
            <button
              type="button"
              className="btn btn-sm2"
              onClick={async () => toast((await copyText(url)) ? `Copied the address for ${h.name}` : 'Couldn’t copy; show it and copy it by hand', 'success')}
              aria-label={`Copy the address for ${h.name}`}
            >
              <Icon name="copy" size={14} /> Copy
            </button>
          </div>
        );
      })}
    </section>
  );
}

const codeOk = (s) => {
  const v = String(s || '').trim();
  return /^MT:[0-9A-Z.\-]{10,}$/i.test(v) || /^\d{11}$/.test(v.replace(/[\s-]/g, '')) || /^\d{21}$/.test(v.replace(/[\s-]/g, ''));
};

/**
 * "Add a device" by its setup code (a connection whose catalog entry has addByCode). The code goes to the hub
 * once and is never saved; progress comes back in the connection's adminDetails.adding as the list refreshes.
 */
const startOf = (a) => (a && (a.startedAt || a.at)) || 0;

/** Seconds as 0:07 / 2:31. */
const clock = (s) => {
  const n = Math.max(0, Math.floor(s));
  return `${Math.floor(n / 60)}:${String(n % 60).padStart(2, '0')}`;
};

export function AddByCodeDialog({ installed, spec, title, onClose, onChanged }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const latest = (installed.adminDetails && installed.adminDetails.adding) || null;
  // What this dialog follows: the hub's attempt it started, or (on opening) one still running or one that
  // ended while the dialog was closed and hasn't been shown yet.
  const [started, setStarted] = useState(() =>
    latest && (latest.state === 'working' || ((latest.state === 'done' || latest.state === 'error') && latest.seen === false)) ? latest : null,
  );
  const adding = started ? (latest && startOf(latest) >= startOf(started) && latest.at >= started.at ? latest : started) : null;
  const working = !!adding && adding.state === 'working';
  // While it works, keep the connection fresh (its adminDetails.adding moves on).
  useEffect(() => {
    if (!working) return undefined;
    const t = setInterval(onChanged, 2000);
    return () => clearInterval(t);
  }, [working, onChanged]);
  // The time it's been going: the hub's count, ticking on here between refreshes.
  const [tick, setTick] = useState(() => ({ base: 0, from: Date.now(), now: Date.now() }));
  const hubElapsed = working ? adding.elapsed || 0 : null;
  useEffect(() => {
    if (hubElapsed == null) return undefined;
    setTick({ base: hubElapsed, from: Date.now(), now: Date.now() });
    const t = setInterval(() => setTick((x) => ({ ...x, now: Date.now() })), 1000);
    return () => clearInterval(t);
  }, [hubElapsed]);
  const elapsed = tick.base + (tick.now - tick.from) / 1000;
  // An ended attempt counts as shown once it's on screen here.
  const ended = adding && adding.state !== 'working' && adding.seen === false ? startOf(adding) : null;
  useEffect(() => {
    if (ended == null) return;
    api.post('api/integrations/' + enc(installed.id) + '/adding/seen', {}, { quiet: true }).catch(() => {});
  }, [ended, installed.id]);
  const [cancelling, setCancelling] = useState(false);
  const cancel = async () => {
    setCancelling(true);
    try {
      const res = await api.post('api/integrations/' + enc(installed.id) + '/adding/cancel', {}, { quiet: true });
      setStarted(res.adding);
      onChanged();
    } catch (err) {
      setError(err.message || 'That didn’t work.');
      onChanged();
    } finally {
      setCancelling(false);
    }
  };
  const add = async (e) => {
    e.preventDefault();
    if (!codeOk(code) || busy) return;
    setBusy(true);
    setError('');
    try {
      const res = await api.post('api/integrations/' + enc(installed.id) + '/devices', { code: code.trim() }, { quiet: true });
      setStarted(res.adding);
      setCode('');
      onChanged();
    } catch (err) {
      setError(err.message || 'That didn’t work.');
    } finally {
      setBusy(false);
    }
  };
  const again = () => {
    setStarted(null);
    setError('');
  };
  return (
    <Modal title={title} onClose={onClose}>
      {!adding ? (
        <form className="stack-sm" onSubmit={add}>
          <p className="help">{spec.help || 'The 11-digit setup code or the QR text from the device.'}</p>
          <label className="field-label" htmlFor="add-code">
            {spec.label || 'Setup code'}
          </label>
          <input
            id="add-code"
            className="input"
            autoFocus
            value={code}
            onChange={(e) => (setCode(e.target.value), setError(''))}
            placeholder={spec.placeholder || '3497-011-2332 or MT:Y.K90…'}
            autoComplete="off"
            spellCheck={false}
            inputMode="text"
          />
          {code && !codeOk(code) ? <div className="help amber">That doesn’t look like a setup code yet: 11 digits, or text starting with MT:</div> : null}
          {error ? (
            <div className="error-box" role="alert">
              <span>{error}</span>
            </div>
          ) : null}
          <div className="inline-form">
            <button type="submit" className="btn btn-primary" disabled={busy || !codeOk(code)}>
              {busy ? 'Sending…' : 'Add'}
            </button>
            <button type="button" className="btn" onClick={onClose}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="stack-sm" aria-live="polite">
          <div className={'pair-progress pair-progress--' + adding.state}>
            {adding.state === 'error' ? (
              <Icon name="alert" size={18} />
            ) : adding.state === 'done' ? (
              <Icon name="check" size={18} />
            ) : adding.state === 'cancelled' ? (
              <Icon name="x" size={18} />
            ) : (
              <span className="spinner" style={{ width: 16, height: 16 }} />
            )}
            <span className="pair-progress-text">
              <strong>
                {adding.state === 'error'
                  ? 'It didn’t work'
                  : adding.state === 'done'
                    ? 'Added'
                    : adding.state === 'cancelled'
                      ? 'Stopped'
                      : adding.title || 'Adding the device…'}
              </strong>
              <span className="muted"> {adding.message || ''}</span>
            </span>
            {working ? (
              <span className="pair-elapsed muted small" aria-label={`Going for ${clock(elapsed)}`}>
                {clock(elapsed)}
              </span>
            ) : null}
          </div>
          {error ? (
            <div className="error-box" role="alert">
              <span>{error}</span>
            </div>
          ) : null}
          <div className="inline-form pair-actions">
            {working ? (
              <button type="button" className="btn btn-danger" onClick={cancel} disabled={cancelling}>
                {cancelling ? 'Stopping…' : 'Cancel adding'}
              </button>
            ) : (
              <button type="button" className="btn" onClick={again}>
                {adding.state === 'done' ? 'Add another' : 'Try another code'}
              </button>
            )}
            <button type="button" className="btn btn-primary" onClick={onClose}>
              {working ? 'Close (it carries on)' : 'Done'}
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}

/** The devices a connection paired by code (adminDetails.pairedDevices), each with Remove. */
export function PairedDevices({ installed, devices, onChanged }) {
  const confirm = useConfirm();
  const [busy, setBusy] = useState(null);
  const remove = async (d) => {
    const ok = await confirm({
      title: `Remove ${d.name}?`,
      body: `Newman stops controlling it and forgets it${d.entityIds && d.entityIds.length ? ', and automations that use it stop working' : ''}. Other apps that also control it keep it.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    setBusy(d.id);
    try {
      await api.del('api/integrations/' + enc(installed.id) + '/devices/' + enc(d.id));
      toast(`Removed ${d.name}`, 'success');
      onChanged();
    } catch {
      /* toast shown */
    } finally {
      setBusy(null);
    }
  };
  if (!devices.length) return <Empty title="Nothing paired yet">Devices you add with a setup code are listed here.</Empty>;
  return (
    <ul className="paired-list" aria-label="Paired devices">
      {devices.map((d) => (
        <li key={d.id} className="paired-row">
          <span className={'dot ' + (d.available ? 'dot--teal' : 'dot--danger')} aria-hidden="true" />
          <span className="paired-text">
            <strong>{d.name}</strong>
            <span className="muted small">
              {[d.manufacturer, d.model && d.model !== d.name ? d.model : '', d.network, d.available ? '' : 'not answering'].filter(Boolean).join(' · ')}
            </span>
          </span>
          <button type="button" className="btn btn-sm2 btn-danger" disabled={busy === d.id} onClick={() => remove(d)} aria-label={`Remove ${d.name}`}>
            {busy === d.id ? 'Removing…' : 'Remove'}
          </button>
        </li>
      ))}
    </ul>
  );
}
/**
 * Signing a connection in to Amazon (its adminDetails.amazon): Amazon's own sign-in page opens in a new tab
 * (the password goes to Amazon only), then the address Amazon finished on is pasted back - the hub keeps
 * only the token Amazon swaps it for. Once signed in: when the timers were last read, whose Echos' timers
 * show, and signing out.
 */
export function AmazonSignIn({ installed, details, onChanged }) {
  const [answer, setAnswer] = useState('');
  const [busy, setBusy] = useState(false);
  const base = 'api/integrations/' + enc(installed.id);
  const echos = details.echos || [];

  const connect = async () => {
    setBusy(true);
    try {
      await api.post(base + '/sign-in', { answer: answer.trim() });
      toast('Signed in to Amazon', 'success');
      setAnswer('');
      onChanged();
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };
  const signOut = async () => {
    setBusy(true);
    try {
      await api.post(base + '/sign-out');
      toast('Signed out of Amazon', 'success');
      onChanged();
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };
  const show = async (serial, on) => {
    const hidden = echos.filter((e) => (e.serial === serial ? !on : !e.shown)).map((e) => e.serial);
    setBusy(true);
    try {
      await api.patch(base, { config: { hidden_echos: hidden } });
      onChanged();
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };

  if (!details.signedIn) {
    return (
      <div className="amazon-signin">
        <div className="amazon-step">
          <div className="field-label">1. Sign in on Amazon's page</div>
          <div className="amazon-row">
            <a className="btn btn-primary" href={details.signInUrl} target="_blank" rel="noopener noreferrer">
              Open Amazon sign-in
            </a>
            <span className="help">Opens in a new tab. Your password goes to Amazon only.</span>
          </div>
        </div>
        <div className="amazon-step">
          <label className="field-label" htmlFor="amazon-landing">
            2. Paste the address of the page Amazon finishes on
          </label>
          <input
            id="amazon-landing"
            className="input"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            placeholder="https://www.amazon.com/ap/maplanding?…"
            autoComplete="off"
            spellCheck={false}
            disabled={busy}
          />
        </div>
        <div>
          <button type="button" className="btn btn-primary" disabled={busy || !answer.trim()} onClick={connect}>
            {busy ? 'Connecting…' : 'Connect'}
          </button>
        </div>
      </div>
    );
  }
  return (
    <div className="amazon-signin">
      <div className="amazon-ok" role="status">
        <Icon name="check" size={16} strokeWidth={2.6} />
        <span>Signed in · {details.checkedAt ? 'timers checked ' + fmtAgo(details.checkedAt) : 'checking the timers…'}</span>
      </div>
      {echos.length ? (
        <div className="amazon-step">
          <div className="amazon-head">Show timers from</div>
          {echos.map((e) => (
            <Check key={e.serial} checked={e.shown} disabled={busy} onChange={(v) => show(e.serial, v)}>
              {e.name}
            </Check>
          ))}
        </div>
      ) : null}
      <div>
        <button type="button" className="btn btn-danger-text" disabled={busy} onClick={signOut}>
          Sign out of Amazon
        </button>
      </div>
    </div>
  );
}
