// Dashboards: what the wall tablet and the browser show. Everyone arranges their own; admins also
// see and arrange other accounts' (a wall tablet's account can't edit its own). "Shown on" counts only
// sign-ins the hub says are live (session.live), so a tablet's old, abandoned sign-ins don't pile up there.
// Admins also get the Phone Home tab: the household layout every phone's Home starts from, and who may
// change their own (components/PhoneHome.jsx).

import { useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading, Modal, MoreMenu, Tabs } from '../components/Controls.jsx';
import PhoneHome from '../components/PhoneHome.jsx';
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
  const [tab, setTab] = useState('wall');

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
        {tab === 'wall' ? (
          <button type="button" className="btn btn-primary" onClick={() => setModal({ kind: 'new' })}>
            <Icon name="plus" size={16} /> New dashboard
          </button>
        ) : null}
      </div>
      {isAdmin ? (
        <div className="conn-tabs dash-tabs">
          <Tabs label="Dashboards" value={tab} onChange={setTab} tabs={[{ id: 'wall', label: 'Wall tablets' }, { id: 'phone', label: 'Phone Home' }]} />
        </div>
      ) : null}
      {isAdmin && tab === 'phone' ? (
        <div className="pane">
          <div className="pane-body">
            <PhoneHome />
          </div>
        </div>
      ) : (
        <div className="pane pane--plain">
          <div className="pane-body pane-body--flush-x">
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

          </div>
        </div>
      )}
      {modal && modal.kind === 'new' ? <NameModal title="New dashboard" initial="Wall tablet" label="Create" owners={isAdmin ? tablets : null} onClose={() => setModal(null)} onSave={create} /> : null}
      {modal && modal.kind === 'rename' ? <NameModal title="Rename dashboard" initial={modal.d.name} onClose={() => setModal(null)} onSave={(n) => rename(modal.d, n)} /> : null}
    </div>
  );
}
