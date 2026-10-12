// Dashboards: what the wall tablet, the browser and the phones show. Two tables:
//  - Wall & browser dashboards: everyone arranges their own; admins also see and arrange other
//    accounts' (a wall tablet's account can't edit its own). "Shown on" counts only sign-ins the
//    hub says are live (session.live), so a tablet's old, abandoned sign-ins don't pile up there.
//  - Phone layouts: the household default (and who uses it) and each customized phone. Admins
//    arrange the default and anyone's; others only their own.

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

/** "3 devices": the names on hover, and in a little list on click. */
function DeviceCount({ names }) {
  if (!names.length) return <span className="muted">—</span>;
  return (
    <details className="dash-shown">
      <summary title={names.join('\n')}>{plural(names.length, 'device')}</summary>
      <ul className="dash-shown-list">
        {names.map((n, i) => (
          <li key={i}>{n}</li>
        ))}
      </ul>
    </details>
  );
}

/** A phone layout's tab names ("Overview, Security, Lights"). */
function tabNames(layout) {
  return ((layout && layout.pages) || []).filter((p) => p.tab).map((p) => p.name);
}

function TabsCell({ layout }) {
  const tabs = tabNames(layout);
  return tabs.length ? <span>{tabs.join(', ')}</span> : <span className="muted">None</span>;
}

/** A customized phone's tabs (its layout is fetched on its own). */
function PersonTabs({ userId }) {
  const res = useApi('api/phone-layouts/' + enc(userId));
  if (!res.data) return <span className="muted">{res.error ? '?' : '…'}</span>;
  return <TabsCell layout={res.data.layout} />;
}

/** The phone layouts table: the household default and each customized phone (admins), or just your phone. */
function PhoneLayouts({ isAdmin, me }) {
  const confirm = useConfirm();
  const res = useApi(isAdmin ? 'api/phone-layouts' : 'api/phone-layouts/me');
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
  const resetAll = async (custom) => {
    const ok = await confirm({
      title: 'Reset everyone to the default?',
      body:
        custom.length === 1
          ? `${custom[0].name}’s customized phone goes back to the household default.`
          : `All ${custom.length} customized phones (${custom.map((p) => p.name).join(', ')}) go back to the household default.`,
      confirmLabel: 'Reset',
      danger: true,
    });
    if (!ok) return;
    try {
      const r = await api.post('api/phone-layouts/reset-all', {});
      toast(`${plural(r.reset || 0, 'phone')} back on the household default`, 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };

  if (!res.data) return res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />;

  const head = (
    <thead>
      <tr>
        <th scope="col">Name</th>
        <th scope="col">Tabs</th>
        <th scope="col">Used by</th>
        <th scope="col" className="dash-col-actions">
          <span className="sr-only">Actions</span>
        </th>
      </tr>
    </thead>
  );

  if (!isAdmin) {
    const own = res.data.source === 'own';
    return (
      <div className="table-wrap">
        <table className="dtable">
          {head}
          <tbody>
            <tr>
              <td>
                <a className="dtable-name" href="#/dashboards/phone/me">
                  {own ? 'Your phone' : 'Household default'}
                </a>
              </td>
              <td>
                <TabsCell layout={res.data.layout} />
              </td>
              <td>You</td>
              <td className="dash-col-actions">
                <span className="dtable-actions">
                  <a className="btn btn-sm" href="#/dashboards/phone/me">
                    {own ? 'Arrange' : 'Customize'}
                  </a>
                  {own ? <MoreMenu label="More for your phone" items={[{ label: 'Reset to the default', onClick: () => reset('api/phone-layouts/me/reset') }]} /> : null}
                </span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    );
  }

  const people = res.data.people || [];
  const onDefault = people.filter((p) => p.source !== 'own');
  const custom = people.filter((p) => p.source === 'own');
  const you = (p) => (me && p.userId === me.id ? ' (you)' : '');
  return (
    <div className="table-wrap">
      <table className="dtable">
        {head}
        <tbody>
          <tr>
            <td>
              <a className="dtable-name" href="#/dashboards/phone/default">
                Household default
              </a>
            </td>
            <td>
              <TabsCell layout={res.data.default} />
            </td>
            <td>{onDefault.length ? onDefault.map((p) => p.name + you(p)).join(', ') : <span className="muted">Nobody</span>}</td>
            <td className="dash-col-actions">
              <span className="dtable-actions">
                <a className="btn btn-sm" href="#/dashboards/phone/default">
                  Arrange
                </a>
                <MoreMenu
                  label="More for the household default"
                  items={[
                    ...onDefault.map((p) => ({ label: `Customize for ${p.name}${you(p)}`, onClick: () => navigate('dashboards/phone/' + enc(p.userId)) })),
                    ...(custom.length ? [{ label: 'Reset everyone to the default', danger: true, onClick: () => resetAll(custom) }] : []),
                  ]}
                />
              </span>
            </td>
          </tr>
          {custom.map((p) => {
            const href = '#/dashboards/phone/' + enc(p.userId);
            return (
              <tr key={p.userId}>
                <td>
                  <a className="dtable-name" href={href}>
                    {p.name}’s phone
                  </a>
                </td>
                <td>
                  <PersonTabs userId={p.userId} />
                </td>
                <td>
                  {p.name}
                  {you(p)}
                </td>
                <td className="dash-col-actions">
                  <span className="dtable-actions">
                    <a className="btn btn-sm" href={href}>
                      Arrange
                    </a>
                    <MoreMenu label={`More for ${p.name}’s phone`} items={[{ label: 'Reset to the default', onClick: () => reset('api/phone-layouts/' + enc(p.userId) + '/reset', p.name) }]} />
                  </span>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
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
    // Only sign-ins in use: the hub keeps a tablet's old sign-ins until they expire, a year on.
    const s = (sessions.data || []).filter((x) => x.live);
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
        <button type="button" className="btn btn-primary" onClick={() => setModal({ kind: 'new' })}>
          <Icon name="plus" size={16} /> New dashboard
        </button>
      </div>
      <div className="pane pane--plain">
        <div className="pane-body pane-body--flush-x">
          <h2 className="dash-section">Wall &amp; browser</h2>
          {!items ? (
            list.error ? <ErrorBox error={list.error} onRetry={list.reload} /> : <Loading />
          ) : !items.length ? (
            <Empty title="Setting up your first dashboard…">Newman lays it out from your devices.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="dtable">
                <thead>
                  <tr>
                    <th scope="col">Name</th>
                    <th scope="col">Pages</th>
                    <th scope="col">Default for</th>
                    <th scope="col">Shown on</th>
                    <th scope="col" className="dash-col-actions">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {items.map((d) => {
                    const pages = d.pages || [];
                    const owner = ownerOf(d);
                    return (
                      <tr key={d.id}>
                        <td>
                          <a className="dtable-name" href={'#/dashboards/' + enc(d.id)}>
                            {d.name}
                          </a>
                          {!d.isDefault ? <span className="dtable-sub">{whose(d)}</span> : null}
                        </td>
                        <td>
                          <span title={pages.map((p) => p.name).join(', ')}>{pages.length}</span>
                        </td>
                        <td>{d.isDefault ? (owner ? owner.name : 'You') : <span className="muted">—</span>}</td>
                        <td>
                          <DeviceCount names={devicesFor(d)} />
                        </td>
                        <td className="dash-col-actions">
                          <span className="dtable-actions">
                            <a className="btn btn-sm" href={'#/dashboards/' + enc(d.id)}>
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
                          </span>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
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

          <h2 className="dash-section dash-section--next">Phone app</h2>
          <PhoneLayouts isAdmin={isAdmin} me={me} />
        </div>
      </div>
      {modal && modal.kind === 'new' ? <NameModal title="New dashboard" initial="Wall tablet" label="Create" owners={isAdmin ? tablets : null} onClose={() => setModal(null)} onSave={create} /> : null}
      {modal && modal.kind === 'rename' ? <NameModal title="Rename dashboard" initial={modal.d.name} onClose={() => setModal(null)} onSave={(n) => rename(modal.d, n)} /> : null}
    </div>
  );
}
