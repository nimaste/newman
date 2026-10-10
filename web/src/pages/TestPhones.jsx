// Developer tools inside Phones: pretend phones for trying automations end to
// end, and the outbox of what they would have received.

import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading } from '../components/Controls.jsx';
import { NotificationBubble, NotificationButtons } from '../components/Notification.jsx';
import { Icon } from '../icons.jsx';
import { fmtWhen } from '../format.js';

function randomId(prefix) {
  const bytes = new Uint8Array(8);
  (window.crypto || {}).getRandomValues ? window.crypto.getRandomValues(bytes) : bytes.forEach((_, i) => (bytes[i] = Math.floor(Math.random() * 256)));
  return prefix + Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Outbox entries are the push payload (all strings) plus the target device; accept a wrapped or flat shape. */
function normalizeItem(item) {
  const p = (item && (item.payload || item.data)) || item || {};
  let buttons = p.buttons;
  if (typeof buttons === 'string') {
    try {
      buttons = JSON.parse(buttons);
    } catch {
      buttons = [];
    }
  }
  const ts = item.ts || item.sentAt || item.createdAt || p.ts || null;
  return {
    key: [item.id, p.run_id, p.kind, ts, item.deviceId || p.deviceId, p.action_token && String(p.action_token).slice(-12)].filter(Boolean).join('|') || 'n',
    deviceId: item.deviceId || p.deviceId || p.device_id || '',
    ts,
    p,
    buttons: Array.isArray(buttons) ? buttons : [],
  };
}

/** The "Developer: test phones" pane: create a test phone, and the outbox. */
export default function TestPhoneTools({ people, phones, onChanged }) {
  const confirm = useConfirm();
  const [deviceFilter, setDeviceFilter] = useState('');
  const outbox = useApi('api/test/outbox' + (deviceFilter ? '?deviceId=' + enc(deviceFilter) : ''), { interval: 3000 });
  const [personId, setPersonId] = useState('');
  const [name, setName] = useState('');
  const [creating, setCreating] = useState(false);
  const [pressing, setPressing] = useState({});
  const [results, setResults] = useState({});

  const testPhones = (phones || []).filter((p) => p.platform === 'test');
  const personName = (id) => {
    const p = (people || []).find((x) => x.id === id);
    return p ? p.name : null;
  };
  const phoneName = (deviceId) => {
    const p = (phones || []).find((x) => x.deviceId === deviceId);
    return p ? p.name || 'Test phone' : deviceId || 'Unknown phone';
  };

  const create = async (e) => {
    e.preventDefault();
    setCreating(true);
    const who = personName(personId);
    const friendlyName = name.trim() || `Test phone${who ? ' (' + who + ')' : ''}`;
    try {
      await api.post('api/devices/register', {
        token: randomId('test-token-'),
        platform: 'test',
        deviceId: randomId('test-'),
        friendlyName,
        deviceModel: 'Newman dashboard',
        personId: personId || null,
      });
      toast(`Created ${friendlyName}`, 'success');
      setName('');
      onChanged();
    } catch {
      /* toast shown */
    } finally {
      setCreating(false);
    }
  };

  const clear = async () => {
    try {
      await api.del('api/test/outbox');
      toast('Outbox cleared', 'success');
      setResults({});
    } catch {
      /* toast shown */
    }
    outbox.reload();
  };

  const press = async (n, b) => {
    if (b.id === 'open') {
      toast('“Open Newman” just opens the app — nothing is sent to the hub.', 'info');
      setResults((r) => ({ ...r, [n.key]: { ok: true, text: 'Opened the app' } }));
      return;
    }
    if (b.sensitive) {
      const ok = await confirm({
        title: `${b.label}?`,
        body: 'On a real phone this asks for Face ID or a fingerprint first. Continue as if it succeeded?',
        confirmLabel: b.label,
        danger: true,
        sensitive: true,
      });
      if (!ok) return;
    }
    setPressing((s) => ({ ...s, [n.key]: b.id }));
    try {
      await api.post(
        'api/public/actions',
        {
          button: b.id,
          subject: n.p.action_subject,
          deviceId: n.deviceId,
          exp: n.p.action_exp,
          token: n.p.action_token,
        },
        { quiet: true },
      );
      setResults((r) => ({ ...r, [n.key]: { ok: true, text: `${b.label} — accepted` } }));
      toast(`${b.label}: accepted by the hub`, 'success');
    } catch (err) {
      const text = err.status === 401 ? 'Rejected: bad or expired token' : err.message;
      setResults((r) => ({ ...r, [n.key]: { ok: false, text: `${b.label} — ${text}` } }));
      toast(`${b.label}: ${text}`, 'error');
    } finally {
      setPressing((s) => ({ ...s, [n.key]: null }));
      outbox.reload();
    }
  };

  const seen = {};
  const items = (outbox.data || []).map((item) => {
    const n = normalizeItem(item);
    seen[n.key] = (seen[n.key] || 0) + 1;
    return seen[n.key] > 1 ? { ...n, key: n.key + '#' + seen[n.key] } : n;
  });

  return (
    <section className="pane" aria-labelledby="dev-title">
      <div className="pane-tools pane-tools--between">
        <h2 id="dev-title" className="eyebrow">
          Developer: test phones
        </h2>
        <button type="button" className="btn btn-sm" onClick={clear} disabled={!items.length}>
          <Icon name="trash" size={14} /> Clear outbox
        </button>
      </div>
      <div className="pane-body stack">
        <p className="help" style={{ margin: 0 }}>
          Pretend phones for trying automations end to end. They never get real pushes — what they would receive lands in the outbox below, and
          tapping a button calls the hub exactly like the app does. They show up in the list like any other phone.
        </p>
        <form className="inline-form tp-create" onSubmit={create} aria-label="Create a test phone">
          <label className="sr-only" htmlFor="tp-person">
            Belongs to
          </label>
          <select id="tp-person" className="select select--sm" value={personId} onChange={(e) => setPersonId(e.target.value)}>
            <option value="">Belongs to nobody yet</option>
            {(people || []).map((p) => (
              <option key={p.id} value={p.id}>
                For {p.name}
              </option>
            ))}
          </select>
          <label className="sr-only" htmlFor="tp-name">
            Name
          </label>
          <input
            id="tp-name"
            className="input input--sm"
            value={name}
            placeholder={`Test phone${personName(personId) ? ' (' + personName(personId) + ')' : ''}`}
            onChange={(e) => setName(e.target.value)}
          />
          <button type="submit" className="btn btn-sm" disabled={creating}>
            <Icon name="plus" size={14} /> {creating ? 'Creating…' : 'Create test phone'}
          </button>
        </form>

        <div className="stack-sm">
          <div className="inline-form" style={{ justifyContent: 'space-between' }}>
            <h3 className="group-title" style={{ margin: 0 }}>
              Outbox {outbox.data ? `(${items.length})` : ''}
            </h3>
            {testPhones.length > 1 ? (
              <label className="inline-form">
                <span className="muted">Show</span>
                <select className="select select--sm" value={deviceFilter} onChange={(e) => setDeviceFilter(e.target.value)} aria-label="Show notifications for">
                  <option value="">All test phones</option>
                  {testPhones.map((ph) => (
                    <option key={ph.deviceId} value={ph.deviceId}>
                      {ph.name || 'Test phone'}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
          </div>
          {outbox.data === undefined ? (
            outbox.error ? <ErrorBox error={outbox.error} onRetry={outbox.reload} /> : <Loading>Loading outbox…</Loading>
          ) : !items.length ? (
            <Empty title="Outbox is empty">
              {testPhones.length ? 'Notifications sent to test phones show up here within a few seconds.' : 'Create a test phone, then run an automation with “Test run”.'}
            </Empty>
          ) : (
            <div className="outbox">
              {items.map((n) => {
                const p = n.p;
                const res = results[n.key];
                return (
                  <article key={n.key} className="outbox-card" aria-label={p.title || 'Notification'}>
                    <div className="outbox-meta">
                      <span>{phoneName(n.deviceId)}</span>
                      <span>
                        {p.kind || 'notification'}
                        {n.ts ? ' · ' + fmtWhen(n.ts) : ''}
                      </span>
                    </div>
                    <NotificationBubble
                      tag={'Newman' + (p.time_sensitive === 'true' || p.time_sensitive === true ? ' · Time sensitive' : '')}
                      title={p.title}
                      body={p.body}
                      image={p.snapshot_url || null}
                    />
                    <NotificationButtons buttons={n.buttons} busyId={pressing[n.key]} onPress={(b) => press(n, b)} />
                    {res ? (
                      <div className="outbox-meta" role="status" style={{ color: res.ok ? '#9fd8cf' : '#f3b49f' }}>
                        {res.text}
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </section>
  );
}
