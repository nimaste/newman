import { useState } from 'react';
import { enc } from '../api.js';
import { navigate, useApi, useHub, useRoute } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { Check, Empty, ErrorBox, Loading, Segmented } from '../components/Controls.jsx';
import { DeviceControls, EntityName, useDeviceActions } from '../components/DeviceControls.jsx';
import { StateText } from '../components/DeviceList.jsx';
import { DeviceTileGroups } from '../components/DeviceTiles.jsx';
import RemoveDevicesDialog, { isOffline } from '../components/RemoveDevices.jsx';
import { Icon } from '../icons.jsx';
import { groupByArea, issuesOf } from '../model.js';
import { plural } from '../format.js';

/** Filters live in the URL: #/devices?c=lighting&t=light&s=offline&i=lutron&q=kitchen */
function setFilters(next) {
  const q = new URLSearchParams();
  if (next.c) q.set('c', next.c);
  if (next.t) q.set('t', next.t);
  if (next.s) q.set('s', next.s);
  if (next.i) q.set('i', next.i);
  if (next.q) q.set('q', next.q);
  if (next.v) q.set('v', next.v);
  const s = q.toString();
  navigate('devices' + (s ? '?' + s : ''), { replace: true });
}

// Status filter: what the hub reports for each device (offline, and its health issues - the same ones
// "Needs attention" lists).
const STATUSES = [
  { id: 'online', label: 'Online', test: (e) => !isOffline(e) },
  { id: 'offline', label: 'Offline or unavailable', test: isOffline },
  { id: 'attention', label: 'Needs attention', test: (e) => issuesOf(e).length > 0 },
];

export default function Devices() {
  const { entities, entitiesError, kinds, kindsById, categories, reloadEntities } = useHub();
  const { isAdmin } = useAuth();
  const { query } = useRoute();
  const autos = useApi('api/automations');
  const conns = useApi('api/integrations');
  const { run, rename, busy } = useDeviceActions();
  const [picked, setPicked] = useState(() => new Set());
  const [confirming, setConfirming] = useState(null); // the devices being removed

  if (!entities) {
    return (
      <div className="page">
        <h1 className="page-title">Devices</h1>
        {entitiesError ? <ErrorBox error={entitiesError} onRetry={reloadEntities} /> : <Loading>Loading devices…</Loading>}
      </div>
    );
  }

  const catOf = (e) => (kindsById[e.kind] ? kindsById[e.kind].category : e.category);
  // Every installed connection by its name, plus any a device names that isn't installed (any more).
  const connList = (Array.isArray(conns.data) ? conns.data : []).map((c) => ({ id: c.id, name: c.name || c.id }));
  for (const e of entities) {
    if (!connList.some((c) => c.id === e.integration)) connList.push({ id: e.integration, name: e.integrationName || e.integration });
  }
  connList.sort((a, b) => a.name.localeCompare(b.name));

  const allCats = categories.filter((c) => entities.some((e) => catOf(e) === c.id));
  const cat = allCats.some((c) => c.id === query.get('c')) ? query.get('c') : '';
  const catKinds = cat ? kinds.filter((k) => k.category === cat && entities.some((e) => e.kind === k.id)) : [];
  const type = catKinds.some((k) => k.id === query.get('t')) ? query.get('t') : '';
  const status = STATUSES.find((s) => s.id === query.get('s')) || null;
  const conn = connList.some((c) => c.id === query.get('i')) ? query.get('i') : '';
  const search = query.get('q') || '';
  const needle = search.trim().toLowerCase();

  // Each filter's counts are worked out with the other filters applied.
  const passes = (e, skip) => {
    if (skip !== 'c' && cat && catOf(e) !== cat) return false;
    if (skip !== 'c' && type && e.kind !== type) return false;
    if (skip !== 's' && status && !status.test(e)) return false;
    if (skip !== 'i' && conn && e.integration !== conn) return false;
    if (!needle) return true;
    const area = (e.attrs && e.attrs.area) || '';
    return e.name.toLowerCase().includes(needle) || area.toLowerCase().includes(needle);
  };
  const shown = entities.filter((e) => passes(e));
  const forCat = entities.filter((e) => passes(e, 'c'));
  const forStatus = entities.filter((e) => passes(e, 's'));
  const forConn = entities.filter((e) => passes(e, 'i'));
  const cats = allCats.map((c) => ({ ...c, count: forCat.filter((e) => catOf(e) === c.id).length }));

  // Group by room once narrowed down (Lutron lights/remotes know their room).
  const groups = cat || needle || conn ? groupByArea(shown) : [{ area: null, items: shown }];
  const view = query.get('v') === 'table' ? 'table' : 'tiles';
  const keep = { c: cat, t: type, s: status ? status.id : '', i: conn, q: search, v: view === 'table' ? 'table' : '' };
  const autoNames = Object.fromEntries((autos.data || []).map((a) => [a.id, a.name]));

  // Removing: admins, with the list narrowed to offline devices.
  const removable = !!(isAdmin && status && status.id === 'offline');
  const chosen = removable ? shown.filter((e) => picked.has(e.id)) : [];
  const allChosen = removable && shown.length > 0 && chosen.length === shown.length;
  const toggle = (id) =>
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const selectAll = (on) => setPicked(on ? new Set(shown.map((e) => e.id)) : new Set());

  return (
    <div className="app-page">
      <div className="app-head">
      <h1 className="page-title">Devices</h1>

      <div className="filter-bar filter-bar--inline filter-bar--devices" role="search">
        <label className="filter">
          <span className="filter-label">Category</span>
          <select className="select" value={cat} onChange={(ev) => setFilters({ ...keep, c: ev.target.value, t: '' })}>
            <option value="">All devices ({forCat.length})</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.count})
              </option>
            ))}
          </select>
        </label>
        {catKinds.length > 1 ? (
          <label className="filter">
            <span className="filter-label">Type</span>
            <select className="select" value={type} onChange={(ev) => setFilters({ ...keep, t: ev.target.value })}>
              <option value="">All types</option>
              {catKinds.map((k) => (
                <option key={k.id} value={k.id}>
                  {k.plural || k.name} ({forCat.filter((e) => e.kind === k.id).length})
                </option>
              ))}
            </select>
          </label>
        ) : null}
        <label className="filter">
          <span className="filter-label">Status</span>
          <select className="select" value={status ? status.id : ''} onChange={(ev) => setFilters({ ...keep, s: ev.target.value })}>
            <option value="">Any status ({forStatus.length})</option>
            {STATUSES.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label} ({forStatus.filter(s.test).length})
              </option>
            ))}
          </select>
        </label>
        <label className="filter">
          <span className="filter-label">Connection</span>
          <select className="select" value={conn} onChange={(ev) => setFilters({ ...keep, i: ev.target.value })}>
            <option value="">All connections ({forConn.length})</option>
            {connList.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({forConn.filter((e) => e.integration === c.id).length})
              </option>
            ))}
          </select>
        </label>
        <label className="filter filter--grow">
          <span className="filter-label">Search</span>
          <span className="search-box">
            <input
              className="input"
              type="search"
              placeholder="Name or room"
              value={search}
              onChange={(ev) => setFilters({ ...keep, q: ev.target.value })}
            />
          </span>
        </label>
      </div>

      <Segmented
        label="View"
        value={view}
        onChange={(v) => setFilters({ ...keep, v: v === 'table' ? 'table' : '' })}
        options={[
          { id: 'tiles', label: 'Tiles' },
          { id: 'table', label: 'Table' },
        ]}
      />
      </div>

      {removable && shown.length ? (
        <div className="bulk-bar" role="toolbar" aria-label="Remove devices">
          <Check checked={allChosen} onChange={selectAll}>
            Select all ({shown.length})
          </Check>
          <button type="button" className="btn btn-sm btn-danger" disabled={!chosen.length} onClick={() => setConfirming(chosen)}>
            Remove {chosen.length ? plural(chosen.length, 'device') : 'devices'}…
          </button>
        </div>
      ) : null}

      {confirming ? (
        <RemoveDevicesDialog
          devices={confirming}
          onClose={() => setConfirming(null)}
          onDone={() => {
            setConfirming(null);
            setPicked(new Set());
            if (reloadEntities) reloadEntities();
          }}
        />
      ) : null}

      {view === 'tiles' ? (
        <div className="pane">
          <div className="pane-body">
            {!entities.length ? (
              <Empty title="No devices yet" action={<a href="#/integrations">Add a connection</a>}>
                Devices appear here as soon as Newman is connected to them.
              </Empty>
            ) : (
              <DeviceTileGroups
                entities={shown}
                run={run}
                busy={busy}
                byRoom
                emptyText="Nothing matches — try another filter or search."
                onSelect={removable ? toggle : undefined}
                selected={removable ? picked : undefined}
              />
            )}
          </div>
        </div>
      ) : (
        <div className="pane"><div className="pane-body pane-body--flush">
          {!entities.length ? (
            <Empty title="No devices yet" action={<a href="#/integrations">Add a connection</a>}>
              Devices appear here as soon as Newman is connected to them.
            </Empty>
          ) : !shown.length ? (
            <Empty title="Nothing matches" action={<button type="button" className="btn btn-sm" onClick={() => setFilters({ v: keep.v })}>Show all devices</button>}>
              Try another filter or search.
            </Empty>
          ) : (
            <div role="table" aria-label="Devices">
              <div className="table-head" role="row">
                <div role="columnheader">Name</div>
                <div role="columnheader" className="col-kind">
                  Type
                </div>
                <div role="columnheader" className="col-integration">
                  Connection
                </div>
                <div role="columnheader">State</div>
                <div role="columnheader">Health</div>
                <div role="columnheader">Used in</div>
                <div role="columnheader" style={{ textAlign: 'right' }}>
                  Control
                </div>
              </div>
              {groups.map((g) => [
                g.area !== null ? (
                  <div key={'area:' + g.area} className="table-group" role="row">
                    <div role="rowheader">
                      {g.area} <span className="muted">· {plural(g.items.length, 'device')}</span>
                    </div>
                  </div>
                ) : null,
                ...g.items.map((e) => {
                  const kind = kindsById[e.kind];
                  const issues = issuesOf(e);
                  const used = e.usedBy || [];
                  return (
                    <div className="table-row" role="row" key={e.id}>
                      <div role="cell" className={removable ? 'cell-pick' : undefined}>
                        {removable ? (
                          <button type="button" role="checkbox" aria-checked={picked.has(e.id)} className="check" aria-label={`Select ${e.name}`} onClick={() => toggle(e.id)}>
                            <span className="box" aria-hidden="true">
                              {picked.has(e.id) ? <Icon name="check" size={14} strokeWidth={3} /> : null}
                            </span>
                          </button>
                        ) : null}
                        <EntityName entity={e} onRename={isAdmin ? (name) => rename(e, name) : null} />
                      </div>
                      <div role="cell" className="muted col-kind">
                        {kind ? kind.name : e.kind}
                      </div>
                      <div role="cell" className="muted col-integration">
                        <a href={'#/integrations/' + enc(e.integration)} className="quiet-link">
                          {e.integrationName || e.integration}
                        </a>
                      </div>
                      <div role="cell">
                        <StateText entity={e} kind={kind} />
                      </div>
                      <div role="cell" className={issues.length ? 'amber' : 'faint'}>
                        {issues.length ? (
                          issues.join(' · ')
                        ) : (
                          <span title="No problems" aria-label="No problems">
                            <Icon name="check" size={15} />
                          </span>
                        )}
                      </div>
                      <div role="cell" className="muted">
                        {used.length ? (
                          <span title={used.map((id) => autoNames[id] || id).join(', ')}>
                            {used.length === 1 ? (
                              <a href={'#/automations/' + enc(used[0])} style={{ fontWeight: 600 }}>
                                {autoNames[used[0]] || '1 automation'}
                              </a>
                            ) : (
                              plural(used.length, 'automation')
                            )}
                          </span>
                        ) : (
                          '—'
                        )}
                      </div>
                      <div role="cell" className="cell-actions">
                        <DeviceControls entity={e} kind={kind} busy={busy[e.id]} run={run} />
                      </div>
                    </div>
                  );
                }),
              ])}
            </div>
          )}
        </div></div>
      )}
    </div>
  );
}
