// Dashboards: what the wall tablet, the browser and the phones show. Two parts:
//  - Wall & browser dashboards: everyone arranges their own; admins also see and arrange other
//    accounts' (a wall tablet's account can't edit its own). Each row says whose it is.
//  - Phone layouts: the household default and, per person, "using the household default" or
//    "customized" (with Reset). Admins arrange the default and anyone's; others only their own.

import { useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useLiveRefresh } from '../live.js';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading, Modal, MoreMenu } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import { plural } from '../format.js';
import { ownerLabel } from './DashboardEditor.jsx';

function NameModal({ title, initial, onClose, onSave, label = 'Save', owners }) {
  const [name, setName] = useState(initial || '');
  const [owner, setOwner] = useState('');
  return (
    <Modal title={title} onClose={onClose}>
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim()) onSave(name.trim(), owner || null);
        }}
      >
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} aria-label="Dashboard name" autoFocus />
        {owners && owners.length ? (
          <label className="field">
            <span className="field-label">For</span>
            <select
              className="select"
              aria-label="Whose dashboard"
              value={owner}
              onChange={(e) => {
                const o = owners.find((x) => x.id === e.target.value);
                if (o && (!name.trim() || name === initial)) setName(o.name);
                setOwner(e.target.value);
              }}
            >
              <option value="">Me</option>
              {owners.map((o) => (
                <option key={o.id} value={o.id}>
                  {ownerLabel(o)}
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <div className="inline-form">
          <button type="submit" className="btn btn-primary" disabled={!name.trim()}>
            {label}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** "Tabs: Overview, Security, Lights · 1 more page" for a phone layout. */
function layoutSummary(layout) {
  const pages = (layout && layout.pages) || [];
  const tabs = pages.filter((p) => p.tab).map((p) => p.name);
  const more = pages.length - tabs.length;
  return `Tabs: ${tabs.join(', ') || 'none'}${more ? ` · ${plural(more, 'more page')}` : ''}${layout && layout.today ? ' · Today card' : ''}`;
}

/** The phone layouts part: the household default and each person (admins), or just your phone. */
function PhoneLayouts({ isAdmin, me }) {
  const res = useApi(isAdmin ? 'api/phone-layouts' : 'api/phone-layouts/me');
  const [askAll, setAskAll] = useState(false);
  const [busy, setBusy] = useState(false);
  useLiveRefresh((m) => m.type === 'dashboard', () => res.reload(), 500);

  const reset = async (path, name) => {
    try {
      await api.post(path, {});
      toast(name ? `${name} is back on the household default` : 'Your phone is back on the household default', 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };
  const resetAll = async () => {
    setBusy(true);
    try {
      const r = await api.post('api/phone-layouts/reset-all', {});
      toast(`${plural(r.reset || 0, 'phone')} back on the household default`, 'success');
      setAskAll(false);
      res.reload();
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };

  if (!res.data) return res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />;

  if (!isAdmin) {
    const own = res.data.source === 'own';
    return (
      <div className="rows">
        <div className="row dash-row">
          <a className="dash-main" href="#/dashboards/phone/me">
            <span className="strong">Your phone</span>
            <span className="phone-meta">
              {own ? 'customized' : 'using the household default'} · {layoutSummary(res.data.layout)}
            </span>
          </a>
          {own ? (
            <button type="button" className="btn-link dash-reset" onClick={() => reset('api/phone-layouts/me/reset')}>
              Reset to default
            </button>
          ) : null}
          <a className="btn btn-sm2" href="#/dashboards/phone/me">
            {own ? 'Arrange' : 'Customize'}
          </a>
        </div>
      </div>
    );
  }

  const people = res.data.people || [];
  const onDefault = people.filter((p) => p.source !== 'own');
  const custom = people.filter((p) => p.source === 'own');
  return (
    <>
      <div className="rows">
        <div className="row dash-row">
          <a className="dash-main" href="#/dashboards/phone/default">
            <span className="strong">Household default</span>
            <span className="phone-meta">
              {layoutSummary(res.data.default)} · {onDefault.length ? 'used by ' + onDefault.map((p) => p.name).join(', ') : 'nobody uses it right now'}
            </span>
          </a>
          <a className="btn btn-sm2" href="#/dashboards/phone/default">
            Arrange
          </a>
        </div>
        {people.map((p) => {
          const own = p.source === 'own';
          const href = '#/dashboards/phone/' + enc(p.userId);
          return (
            <div key={p.userId} className="row dash-row">
              <a className="dash-main" href={href}>
                <span className="strong">
                  {p.name}
                  {me && p.userId === me.id ? <span className="dash-owner"> (you)</span> : null}
                </span>
                <span className="phone-meta">{own ? 'customized' : 'using the household default'}</span>
              </a>
              {own ? (
                <button type="button" className="btn-link dash-reset" onClick={() => reset('api/phone-layouts/' + enc(p.userId) + '/reset', p.name)}>
                  Reset to default
                </button>
              ) : null}
              <a className="btn btn-sm2" href={href}>
                {own ? 'Arrange' : 'Customize'}
              </a>
            </div>
          );
        })}
      </div>
      {custom.length ? (
        askAll ? (
          <div className="dash-confirm" role="group" aria-label="Reset everyone">
            <span>
              {custom.length === 1
                ? `Reset ${custom[0].name}’s customized phone to the household default?`
                : `Reset all ${custom.length} customized phones (${custom.map((p) => p.name).join(', ')}) to the household default?`}
            </span>
            <button type="button" className="btn btn-sm2 btn-danger" disabled={busy} onClick={resetAll}>
              Yes, reset
            </button>
            <button type="button" className="btn btn-sm2" onClick={() => setAskAll(false)}>
              Cancel
            </button>
          </div>
        ) : (
          <button type="button" className="btn-link dash-quiet" onClick={() => setAskAll(true)}>
            Reset everyone to the default
          </button>
        )
      ) : null}
    </>
  );
}

export default function Dashboards() {
  const confirm = useConfirm();
  const { user: me, isAdmin } = useAuth();
  const list = useApi(isAdmin ? 'api/dashboards?all=1' : 'api/dashboards');
  const sessions = useApi(isAdmin ? 'api/auth/sessions?all=1' : 'api/auth/sessions');
  const users = useApi(isAdmin ? 'api/users' : null);
  const userById = Object.fromEntries((users.data || []).map((u) => [u.id, u]));
  const tablets = (users.data || []).filter((u) => u.role === 'tablet' && !u.disabled);
  const ownerOf = (d) => (d.userId && me && d.userId !== me.id ? userById[d.userId] || { id: d.userId, name: 'Another account' } : null);
  const [modal, setModal] = useState(null); // { kind: 'new' | 'rename', d? }
  const seeded = useRef(false);

  // First visit: the starter the tablet would get (the hub lays it out from the house's devices).
  useEffect(() => {
    if (seeded.current || !Array.isArray(list.data) || list.data.some((d) => !ownerOf(d))) return;
    seeded.current = true;
    api
      .get('api/dashboards/current', { quiet: true })
      .then(() => list.reload())
      .catch(() => {});
  }, [list.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const devicesFor = (d) => {
    const s = sessions.data || [];
    const owner = ownerOf(d);
    const ownerName = owner ? owner.name : me && me.name;
    const pinned = s.filter((x) => x.dashboardId === d.id);
    // Sessions list the account by name: the owner's tablets without a chosen dashboard show its default.
    const byDefault = d.isDefault ? s.filter((x) => !x.dashboardId && x.kind !== 'browser' && x.kind !== 'app' && (!x.user || x.user === ownerName)) : [];
    return [...pinned, ...byDefault].map((x) => x.deviceName || x.kind);
  };
  const full = async (d) => api.get('api/dashboards/' + enc(d.id), { quiet: true });
  const create = async (name, userId) => {
    try {
      const d = await api.post('api/dashboards', { name, ...(userId ? { userId } : {}) });
      setModal(null);
      navigate('dashboards/' + enc(d.id));
    } catch {
      /* toast shown */
    }
  };
  const rename = async (d, name) => {
    try {
      const f = await full(d);
      await api.put('api/dashboards/' + enc(d.id), { name, theme: f.theme, pages: f.pages, names: f.names || {}, home: f.home });
      toast('Renamed', 'success');
      setModal(null);
      list.reload();
    } catch {
      /* toast shown */
    }
  };
  const duplicate = async (d) => {
    try {
      // The hub copies all of it (tab names, Christmas settings and pictures too).
      await api.post('api/dashboards', { copyFrom: d.id, name: `Copy of ${d.name}`.slice(0, 40), ...(ownerOf(d) ? { userId: d.userId } : {}) });
      toast(`Duplicated ${d.name}`, 'success');
      list.reload();
    } catch {
      /* toast shown */
    }
  };
  const makeDefault = async (d) => {
    try {
      await api.post('api/dashboards/' + enc(d.id) + '/default', {});
      toast(`${d.name} is ${ownerOf(d) ? ownerOf(d).name + '’s' : 'your'} default`, 'success');
      list.reload();
    } catch {
      /* toast shown */
    }
  };
  const remove = async (d) => {
    const shown = devicesFor(d);
    if (!(await confirm({ title: `Delete “${d.name}”?`, body: shown.length ? `It's showing on ${shown.join(', ')}; they'll switch to the default.` : 'This can’t be undone.', confirmLabel: 'Delete', danger: true }))) return;
    try {
      await api.del('api/dashboards/' + enc(d.id));
      toast(`Deleted ${d.name}`, 'success');
      list.reload();
    } catch {
      /* toast shown */
    }
  };

  // Mine first, then each other account's, by whose it is.
  const whose = (d) => (ownerOf(d) ? ownerLabel(ownerOf(d)) : (me && me.name) || 'You');
  const items = Array.isArray(list.data)
    ? list.data.slice().sort((a, b) => {
        const oa = ownerOf(a);
        const ob = ownerOf(b);
        if (!oa !== !ob) return oa ? 1 : -1;
        return oa && ob ? whose(a).localeCompare(whose(b)) : 0;
      })
    : null;
  const bare = items ? tablets.filter((tb) => !items.some((d) => d.userId === tb.id)) : [];
  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Dashboards</h1>
        <span className="muted head-hint">What the wall tablet, the browser and the phones show.</span>
        <button type="button" className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setModal({ kind: 'new' })}>
          <Icon name="plus" size={16} /> New dashboard
        </button>
      </div>
      <div className="pane">
        <div className="pane-body">
          <h2 className="dash-section">Wall &amp; browser dashboards</h2>
          {!items ? (
            list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : <Loading />
          ) : !items.length ? (
            <Empty title="Setting up your first dashboard…">Newman lays it out from your devices.</Empty>
          ) : (
            <div className="rows">
              {items.map((d) => {
                const shown = devicesFor(d);
                const pages = d.pages || [];
                return (
                  <div key={d.id} className="row dash-row">
                    <a className="dash-main" href={'#/dashboards/' + enc(d.id)}>
                      <span className="strong">
                        {d.name}
                        <span className="dash-owner">
                          {' · '}
                          {whose(d)}
                          {d.isDefault ? (ownerOf(d) ? ', its default' : ', your default') : ''}
                        </span>
                      </span>
                      <span className="phone-meta">
                        {plural(pages.length, 'page')}
                        {pages.length ? ': ' + pages.map((p) => p.name).join(', ') : ''} · {shown.length ? 'shown on ' + shown.join(', ') : 'not on any device'}
                      </span>
                    </a>
                    <a className="btn btn-sm2" href={'#/dashboards/' + enc(d.id)}>
                      Arrange
                    </a>
                    <MoreMenu
                      label={`More for ${d.name}`}
                      items={[
                        { label: 'Rename', onClick: () => setModal({ kind: 'rename', d }) },
                        { label: 'Duplicate', onClick: () => duplicate(d) },
                        ...(d.isDefault ? [] : [{ label: 'Make default', onClick: () => makeDefault(d) }]),
                        { label: 'Delete', danger: true, onClick: () => remove(d) },
                      ]}
                    />
                  </div>
                );
              })}
            </div>
          )}
          {bare.length ? (
            <div className="dash-bare">
              {bare.map((tb) => (
                <span key={tb.id} className="dash-bare-item">
                  <span className="muted">{ownerLabel(tb)} has no dashboard yet.</span>
                  <button type="button" className="btn-link" onClick={() => create(tb.name, tb.id)}>
                    Set one up
                  </button>
                </span>
              ))}
            </div>
          ) : null}
          <p className="help" style={{ marginTop: 14 }}>
            A wall tablet shows its account’s default dashboard unless you pick another one for it under Profile → Your devices.
            {isAdmin ? ' A wall tablet account can’t arrange its own: do it here.' : ''}
          </p>

          <h2 className="dash-section dash-section--next">Phone layouts</h2>
          <p className="help dash-section-help">
            {isAdmin
              ? 'The Newman app on phones. Everyone uses the household default unless their phone is customized; customizing starts from a copy of the default.'
              : 'How the Newman app arranges your phone. Customizing starts from a copy of the household default.'}
          </p>
          <PhoneLayouts isAdmin={isAdmin} me={me} />
        </div>
      </div>
      {modal && modal.kind === 'new' ? <NameModal title="New dashboard" initial="Wall tablet" label="Create" owners={isAdmin ? tablets : null} onClose={() => setModal(null)} onSave={create} /> : null}
      {modal && modal.kind === 'rename' ? <NameModal title="Rename dashboard" initial={modal.d.name} onClose={() => setModal(null)} onSave={(n) => rename(modal.d, n)} /> : null}
    </div>
  );
}
