import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useApi, useHub, useRoute } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { Check, Empty, ErrorBox, Loading, Modal, Segmented } from '../components/Controls.jsx';
import { DeviceControls, EntityName, useDeviceActions } from '../components/DeviceControls.jsx';
import { StateText } from '../components/DeviceList.jsx';
import { DeviceTileGroups } from '../components/DeviceTiles.jsx';
import RemoveDevicesDialog, { isOffline } from '../components/RemoveDevices.jsx';
import LightGroupsDialog from '../components/LightGroups.jsx';
import { Icon } from '../icons.jsx';
import { categoryOf, groupByArea, issuesOf, needsLook, roomOf, summaryChips } from '../model.js';
import { plural } from '../format.js';

// Which categories are open, remembered in this browser ({categoryId: true | false}). A category
// you haven't opened or closed yourself is open while something in it needs a look.
const OPEN_KEY = 'newman.devices.open';
function loadOpen() {
  try {
    const v = JSON.parse(localStorage.getItem(OPEN_KEY) || '{}');
    return v && typeof v === 'object' ? v : {};
  } catch {
    return {};
  }
}
function saveOpen(v) {
  try {
    localStorage.setItem(OPEN_KEY, JSON.stringify(v));
  } catch {
    /* storage off (a private window): it just isn't remembered */
  }
}

/** One category: a row that sums it up (name, count, status chips) and opens to its device tiles. */
function CategoryRow({ name, count, chips, open, onToggle, onRename, children }) {
  return (
    <section className={'dcat' + (open ? ' is-open' : '')} aria-label={name}>
      <div className="dcat-top">
        <button type="button" className="dcat-head" aria-expanded={open} onClick={onToggle}>
          <span className="dcat-caret" aria-hidden="true">
            <Icon name="chevronRight" size={16} />
          </span>
          <span className="dcat-name">{name}</span>
          <span className="dcat-count">{count}</span>
          <span className="dcat-chips">
            {chips.map((c, i) => (
              <span key={i} className={'schip schip--' + c.tone} title={c.title || undefined}>
                {c.text}
              </span>
            ))}
          </span>
        </button>
        {onRename ? (
          <button type="button" className="btn-link dcat-rename" onClick={onRename} aria-label={`Rename ${name}`}>
            Rename
          </button>
        ) : null}
      </div>
      {open ? <div className="dcat-body">{children}</div> : null}
    </section>
  );
}

/** Renaming a category made in Newman (a device moved into "New category…"). */
function RenameCategory({ category, onClose }) {
  const [name, setName] = useState(category.name);
  const [saving, setSaving] = useState(false);
  const save = async () => {
    setSaving(true);
    try {
      await api.put('api/categories/' + enc(category.id), { name: name.trim() });
      toast(`Renamed to ${name.trim()}`, 'success');
      onClose();
    } catch {
      setSaving(false);
    }
  };
  return (
    <Modal title={`Rename “${category.name}”`} onClose={onClose}>
      <form
        className="stack-sm"
        onSubmit={(e) => {
          e.preventDefault();
          if (name.trim() && !saving) save();
        }}
      >
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={40} aria-label="Category name" autoFocus />
        <div className="inline-form">
          <button type="submit" className="btn btn-primary" disabled={!name.trim() || saving}>
            Save
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

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
  const [renaming, setRenaming] = useState(null);
  const { query } = useRoute();
  const autos = useApi('api/automations');
  const conns = useApi('api/integrations');
  const { run, rename, busy } = useDeviceActions();
  const [picked, setPicked] = useState(() => new Set());
  const [confirming, setConfirming] = useState(null); // the devices being removed
  const [groupsOpen, setGroupsOpen] = useState(false);
  const [opened, setOpened] = useState(loadOpen);
  // While searching or filtering every category with a match is open; ones you close then stay
  // closed until the filters change (not remembered).
  const [closedNow, setClosedNow] = useState(() => new Set());
  const filterKey = query.toString();
  useEffect(() => setClosedNow(new Set()), [filterKey]);

  if (!entities) {
    return (
      <div className="app-page">
        <div className="app-head">
          <h1 className="page-title">Devices</h1>
        </div>
        {entitiesError ? <ErrorBox error={entitiesError} onRetry={reloadEntities} /> : <Loading>Loading devices…</Loading>}
      </div>
    );
  }

  const catOf = (e) => categoryOf(e, kindsById);
  // Every installed connection by its name, plus any a device names that isn't installed (any more).
  const connList = (Array.isArray(conns.data) ? conns.data : []).map((c) => ({ id: c.id, name: c.name || c.id }));
  for (const e of entities) {
    if (!connList.some((c) => c.id === e.integration)) connList.push({ id: e.integration, name: e.integrationName || e.integration });
  }
  connList.sort((a, b) => a.name.localeCompare(b.name));

  const allCats = categories.filter((c) => entities.some((e) => catOf(e) === c.id));
  const cat = allCats.some((c) => c.id === query.get('c')) ? query.get('c') : '';
  const catKinds = cat ? kinds.filter((k) => entities.some((e) => e.kind === k.id && catOf(e) === cat)) : [];
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
    const area = roomOf(e);
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

  // Tiles: one collapsible row per category. Searching or filtering shows the categories with a
  // match, open.
  const filtering = !!(cat || type || status || conn || needle);
  const lookup = (e) => kindsById[e.kind];
  const known = new Set(categories.map((c) => c.id));
  const rowCats = [...categories, { id: '_other', name: 'Other' }];
  const rows = rowCats
    .map((c) => {
      const inCat = (e) => (c.id === '_other' ? !known.has(catOf(e)) : catOf(e) === c.id);
      const all = entities.filter(inCat);
      const vis = shown.filter(inCat);
      const attention = all.some((e) => needsLook(e, lookup(e)));
      const open = filtering ? !closedNow.has(c.id) : (opened[c.id] ?? attention);
      return { id: c.id, name: c.name, custom: c.builtIn === false, all, vis, open };
    })
    .filter((r) => (filtering ? r.vis.length : r.all.length));
  const allOpen = rows.length > 0 && rows.every((r) => r.open);
  const toggleRow = (r) => {
    if (filtering) {
      setClosedNow((prev) => {
        const next = new Set(prev);
        if (r.open) next.add(r.id);
        else next.delete(r.id);
        return next;
      });
      return;
    }
    setOpened((prev) => {
      const next = { ...prev, [r.id]: !r.open };
      saveOpen(next);
      return next;
    });
  };
  const setAll = (open) => {
    if (filtering) {
      setClosedNow(open ? new Set() : new Set(rows.map((r) => r.id)));
      return;
    }
    setOpened((prev) => {
      const next = { ...prev, ...Object.fromEntries(rows.map((r) => [r.id, open])) };
      saveOpen(next);
      return next;
    });
  };

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Devices</h1>
        {view === 'tiles' && rows.length > 1 ? (
          <button type="button" className="btn" onClick={() => setAll(!allOpen)}>
            {allOpen ? 'Collapse all' : 'Expand all'}
          </button>
        ) : null}
        {isAdmin ? (
          <button type="button" className="btn" onClick={() => setGroupsOpen(true)}>
            Light groups
          </button>
        ) : null}
      </div>
      {groupsOpen ? <LightGroupsDialog onClose={() => setGroupsOpen(false)} /> : null}

      <div className="page-tools">
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
        <div className="pane pane--plain">
          <div className="pane-body pane-body--flush-x">
            {!entities.length ? (
              <div className="card">
                <Empty title="No devices yet" action={<a href="#/integrations">Add a connection</a>}>
                  Devices appear here as soon as Newman is connected to them.
                </Empty>
              </div>
            ) : !rows.length ? (
              <div className="card">
                <Empty title="Nothing matches" action={<button type="button" className="btn btn-sm" onClick={() => setFilters({ v: keep.v })}>Show all devices</button>}>
                  Try another filter or search.
                </Empty>
              </div>
            ) : (
              <div className="dcat-list">
                {rows.map((r) => (
                  <CategoryRow
                    key={r.id}
                    name={r.name}
                    count={filtering && r.vis.length !== r.all.length ? `${r.vis.length} of ${r.all.length}` : plural(r.all.length, 'device')}
                    chips={summaryChips(r.all, kinds)}
                    open={r.open}
                    onToggle={() => toggleRow(r)}
                    onRename={isAdmin && r.custom ? () => setRenaming(r) : undefined}
                  >
                    <DeviceTileGroups
                      entities={r.vis}
                      run={run}
                      busy={busy}
                      emptyText="Nothing matches — try another filter or search."
                      onSelect={removable ? toggle : undefined}
                      selected={removable ? picked : undefined}
                    />
                  </CategoryRow>
                ))}
              </div>
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
      {renaming ? <RenameCategory category={renaming} onClose={() => setRenaming(null)} /> : null}
    </div>
  );
}
