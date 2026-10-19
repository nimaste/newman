// Compact device tiles in multi-column grids: used by Devices, a connection's
// Devices tab and the automation builder's device pickers. Cameras show a live still.

import { useState } from 'react';
import { enc } from '../api.js';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { hasStill } from './Camera.jsx';
import LiveVideo from './LiveVideo.jsx';
import { DeviceControls } from './DeviceControls.jsx';
import { StateText } from './DeviceList.jsx';
import { Empty } from './Controls.jsx';
import { groupByArea, kindIcon } from '../model.js';
import { roomOf } from '../model.js';

/** Group devices by type (kind order from meta), then optionally by room inside each. */
export function groupByKind(list, kinds, kindsById) {
  const groups = kinds.map((k) => ({ key: k.id, title: k.plural || k.name, kind: k, items: list.filter((e) => e.kind === k.id) })).filter((g) => g.items.length);
  const orphans = list.filter((e) => !kindsById[e.kind]);
  if (orphans.length) groups.push({ key: '_other', title: 'Other', items: orphans });
  return groups;
}

/** One device. `href` makes it a link (with its inline control), `onSelect` makes it a pick-able tile. */
export function DeviceTile({ entity: e, kind, href, onSelect, selected, single, control, run, busy, stills = true }) {
  // Anything with a picture shows it, whatever its kind: live video when it has a stream.
  const isCam = stills && (!!e.live || hasStill(e));
  const pic = isCam ? <LiveVideo entity={e} quality="sub" stillHeight={180} className="tile-still" showNote={false} /> : null;
  const room = roomOf(e);
  const sub = [room && !e.name.includes(room) ? room : null, kind ? kind.name : null].filter(Boolean).join(' · ');
  const body = (
    <>
      <span className="tile-body">
        <span className="tile-top">
          {!isCam ? (
            <span className="tile-icon" aria-hidden="true">
              <Icon name={kindIcon(kind)} size={15} />
            </span>
          ) : null}
          <span className="tile-name">{e.name}</span>
        </span>
        <span className="tile-sub">{sub}</span>
        <span className="tile-state">
          <StateText entity={e} kind={kind} />
        </span>
      </span>
    </>
  );

  if (onSelect) {
    return (
      <button
        type="button"
        role={single ? 'radio' : 'checkbox'}
        aria-checked={!!selected}
        className={'dtile dtile--pick' + (isCam ? ' dtile--cam' : '')}
        onClick={() => onSelect(e.id)}
      >
        {pic}
        {body}
        {selected ? (
          <span className="tile-check" aria-hidden="true">
            <Icon name="check" size={13} strokeWidth={3} />
          </span>
        ) : null}
      </button>
    );
  }
  return (
    <div className={'dtile' + (isCam ? ' dtile--cam' : '')}>
      {pic ? (
        <a className="dtile-pic" href={'#/cameras/' + enc(e.id)} aria-label={`Watch ${e.name} live`}>
          {pic}
        </a>
      ) : null}
      <a className="dtile-link" href={href || '#/devices/' + enc(e.id)} aria-label={`${e.name}: open`}>
        {body}
      </a>
      {control && kind && (kind.commands || []).length ? (
        <div className="tile-control">
          <DeviceControls entity={e} kind={kind} busy={busy} run={run} compact />
        </div>
      ) : null}
    </div>
  );
}

/**
 * Grouped grids of tiles. `byRoom` adds room sub-headings inside each type. `onSelect` (with `selected`, a
 * Set of ids) makes them pick-able tiles instead (Devices: choosing offline devices to remove).
 */
export function DeviceTileGroups({ entities, run, busy, byRoom = false, emptyText, onSelect, selected }) {
  const { kinds, kindsById } = useHub();
  if (!entities.length) return <Empty title="No devices">{emptyText || 'Nothing matches.'}</Empty>;
  return (
    <div className="tile-groups">
      {groupByKind(entities, kinds, kindsById).map((g) => (
        <section key={g.key} className="tile-group" aria-label={g.title}>
          <h3 className="group-title">
            {g.title} <span>· {g.items.length}</span>
          </h3>
          {(byRoom ? groupByArea(g.items) : [{ area: null, items: g.items }]).map((a) => (
            <div key={a.area || '_'} className="tile-room">
              {a.area ? <div className="room-title">{a.area}</div> : null}
              <div className={'tile-grid' + (g.items.some(hasStill) ? ' tile-grid--cams' : '')}>
                {a.items.map((e) => (
                  <DeviceTile key={e.id} entity={e} kind={kindsById[e.kind]} control run={run} busy={busy && busy[e.id]}
                    onSelect={onSelect} selected={!!(onSelect && selected && selected.has(e.id))} />
                ))}
              </div>
            </div>
          ))}
        </section>
      ))}
    </div>
  );
}

/**
 * Device picker as a tile grid with a category dropdown and search.
 * include(entity, kind) limits the choices.
 */
export function DeviceTilePicker({ selected, onChange, include, single, label, emptyText }) {
  const { entities, kinds, kindsById, categories, entitiesById } = useHub();
  const [cat, setCat] = useState('');
  const [q, setQ] = useState('');
  const all = (entities || []).filter((e) => include(e, kindsById[e.kind]));
  const catOf = (e) => (kindsById[e.kind] ? kindsById[e.kind].category : e.category);
  const cats = categories.filter((c) => all.some((e) => catOf(e) === c.id));
  const needle = q.trim().toLowerCase();
  const shown = all.filter(
    (e) => (!cat || catOf(e) === cat) && (!needle || e.name.toLowerCase().includes(needle) || roomOf(e).toLowerCase().includes(needle)),
  );
  const missing = selected.filter((id) => !entitiesById[id]);
  const toggle = (id) => {
    if (single) onChange(selected.includes(id) ? [] : [id]);
    else onChange(selected.includes(id) ? selected.filter((x) => x !== id) : [...selected, id]);
  };
  if (!all.length) return <Empty title="No matching devices">{emptyText || 'Connect your devices first.'}</Empty>;
  return (
    <div className="picker2">
      <div className="picker2-bar">
        {cats.length > 1 ? (
          <select className="select select--sm2" value={cat} onChange={(ev) => setCat(ev.target.value)} aria-label="Category">
            <option value="">All ({all.length})</option>
            {cats.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({all.filter((e) => catOf(e) === c.id).length})
              </option>
            ))}
          </select>
        ) : null}
        <input className="input input--sm2" type="search" placeholder="Search…" value={q} onChange={(ev) => setQ(ev.target.value)} aria-label="Search devices" />
        {!single && selected.length ? <span className="picker2-count">{selected.length} chosen</span> : null}
      </div>
      <div className="picker2-grid" role={single ? 'radiogroup' : 'group'} aria-label={label}>
        {missing.map((id) => (
          <button key={id} type="button" role={single ? 'radio' : 'checkbox'} aria-checked="true" className="dtile dtile--pick dtile--missing" onClick={() => toggle(id)}>
            <span className="tile-body">
              <span className="tile-name">Missing device</span>
              <span className="tile-sub">{id} — click to remove</span>
            </span>
          </button>
        ))}
        {groupByKind(shown, kinds, kindsById).map((g) => [
          <div key={'h' + g.key} className="picker2-group">
            {g.title}
          </div>,
          ...g.items.map((e) => (
            <DeviceTile key={e.id} entity={e} kind={kindsById[e.kind]} onSelect={toggle} selected={selected.includes(e.id)} single={single} />
          )),
        ])}
        {!shown.length ? <div className="muted" style={{ gridColumn: '1 / -1' }}>Nothing matches.</div> : null}
      </div>
    </div>
  );
}
