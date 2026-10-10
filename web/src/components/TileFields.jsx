// A tile's settings, drawn generically from the hub's `types[].fields` (GET api/dashboards/tiles).
// Devices are matched by `filter` against what their kind can do (never by kind id):
//   picture / alerts → the kind has a camera · number-command → a number/percent command
//   mode → a house-mode kind · forecast → reports a forecast · agenda → lists upcoming events
//   temperature-setting → a number command in °C/°F · temperature → reports a room temperature
//   (wall/climate.js) · any → everything. A "page" field picks one of this dashboard's pages.

import { useState } from 'react';
import { useApi, useHub } from '../hooks.js';
import { reportsTemperature, setsTemperature } from '../wall/climate.js';
import { Icon } from '../icons.jsx';
import { Check } from './Controls.jsx';

export function matchesFilter(e, kind, filter) {
  if (!kind) return false;
  if (filter === 'picture' || filter === 'alerts') return !!kind.hasCamera;
  if (filter === 'number-command') return (kind.commands || []).some((c) => c.valueType === 'number' || c.valueType === 'percent');
  if (filter === 'mode') return !!kind.isMode;
  if (filter === 'forecast') return !!(e.attrs && 'forecast' in e.attrs);
  if (filter === 'agenda') return !!(e.attrs && 'upcoming' in e.attrs);
  if (filter === 'temperature-setting') return setsTemperature(kind);
  if (filter === 'temperature') return reportsTemperature(e, kind);
  return true;
}

function commandMatches(c, filter) {
  if (filter === 'number') return c.valueType === 'number' || c.valueType === 'percent';
  if (filter === 'state') return c.valueType === 'state';
  return true;
}

/** The value a new tile starts with for each field. */
export function fieldDefaults(spec) {
  const out = {};
  (spec.fields || []).forEach((f) => {
    if (f.default !== undefined) out[f.key] = f.default;
    else if (f.type === 'entities' || f.type === 'readings' || f.type === 'people') out[f.key] = [];
  });
  return out;
}

/** Devices grouped by kind name, for pickers. */
function groups(list, kindsById) {
  const by = new Map();
  list.forEach((e) => {
    const k = kindsById[e.kind];
    const name = (k && (k.plural || k.name)) || 'Other';
    if (!by.has(name)) by.set(name, []);
    by.get(name).push(e);
  });
  return [...by.entries()];
}

/**
 * "Show as": what this dashboard calls the device (saved in the dashboard's `names`, for every
 * page and tile of it). Empty = its real name, which stays the same everywhere else.
 */
function ShowAs({ e, names, onName }) {
  if (!onName || !e) return null;
  return (
    <input
      className="input input--sm tf-showas"
      value={(names && names[e.id]) || ''}
      maxLength={40}
      placeholder={e.name}
      aria-label={`Show ${e.name} as`}
      title="Show as (on this dashboard)"
      onChange={(ev) => onName(e.id, ev.target.value)}
    />
  );
}

function EntitiesField({ field, value, onChange, orderable, names, onName }) {
  const { entities, entitiesById, kindsById } = useHub();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const chosen = (value || []).filter((id) => entitiesById[id]);
  const needle = q.trim().toLowerCase();
  const options = (entities || []).filter((e) => matchesFilter(e, kindsById[e.kind], field.filter) && !chosen.includes(e.id) && (!needle || e.name.toLowerCase().includes(needle)));
  const move = (i, d) => {
    const next = chosen.slice();
    const j = i + d;
    if (j < 0 || j >= next.length) return;
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
  };
  return (
    <div className="tf-entities">
      {chosen.length ? (
        <ol className="tf-chosen">
          {chosen.map((id, i) => (
            <li key={id} className="tf-chosen-row">
              <span className="tf-chosen-name">
                {entitiesById[id].name}
                {orderable && i === 0 ? <span className="tf-first">shown first</span> : null}
              </span>
              {orderable ? (
                <>
                  <button type="button" className="icon-btn" aria-label={`Move ${entitiesById[id].name} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                    ↑
                  </button>
                  <button type="button" className="icon-btn" aria-label={`Move ${entitiesById[id].name} down`} disabled={i === chosen.length - 1} onClick={() => move(i, 1)}>
                    ↓
                  </button>
                </>
              ) : null}
              <button type="button" className="icon-btn" aria-label={`Remove ${entitiesById[id].name}`} onClick={() => onChange(chosen.filter((x) => x !== id))}>
                <Icon name="x" size={14} />
              </button>
              {onName ? (
                <span className="tf-showas-line">
                  <span className="tf-showas-label">Show as</span>
                  <ShowAs e={entitiesById[id]} names={names} onName={onName} />
                </span>
              ) : null}
            </li>
          ))}
        </ol>
      ) : (
        <div className="help">{field.required ? 'None chosen yet.' : 'None chosen.'}</div>
      )}
      {open ? (
        <div className="tf-picker">
          <input className="input input--sm" type="search" placeholder="Search devices…" value={q} onChange={(e) => setQ(e.target.value)} autoFocus aria-label={`Search ${field.label}`} />
          <div className="tf-picker-list">
            {!options.length ? <div className="help">Nothing to add.</div> : null}
            {groups(options, kindsById).map(([name, list]) => (
              <div key={name}>
                <div className="tf-group">{name}</div>
                {list.map((e) => (
                  <button key={e.id} type="button" className="tf-option" onClick={() => onChange([...chosen, e.id])}>
                    <Icon name="plus" size={14} /> {e.name}
                  </button>
                ))}
              </div>
            ))}
          </div>
          <button type="button" className="btn btn-sm2" onClick={() => (setOpen(false), setQ(''))}>
            Done
          </button>
        </div>
      ) : (
        <button type="button" className="add-dev" onClick={() => setOpen(true)}>
          <Icon name="plus" size={14} /> Add
        </button>
      )}
    </div>
  );
}

function EntityField({ field, value, onChange, names, onName }) {
  const { entities, entitiesById, kindsById } = useHub();
  const options = (entities || []).filter((e) => matchesFilter(e, kindsById[e.kind], field.filter));
  return (
    <>
    <select className="select" value={value || ''} onChange={(e) => onChange(e.target.value || null)} aria-label={field.label}>
      <option value="">{field.required ? 'Choose a device…' : 'None'}</option>
      {groups(options, kindsById).map(([name, list]) => (
        <optgroup key={name} label={name}>
          {list.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </optgroup>
      ))}
    </select>
    {value && entitiesById[value] && onName ? (
      <span className="tf-showas-line">
        <span className="tf-showas-label">Show as</span>
        <ShowAs e={entitiesById[value]} names={names} onName={onName} />
      </span>
    ) : null}
    </>
  );
}

function CommandField({ field, value, onChange, config }) {
  const { entitiesById, kindsById } = useHub();
  const e = entitiesById[config.entity];
  const k = e && kindsById[e.kind];
  const cmds = ((k && k.commands) || []).filter((c) => commandMatches(c, field.filter));
  if (!e) return <div className="help">Choose the device first.</div>;
  return (
    <select className="select" value={value || ''} onChange={(ev) => onChange(ev.target.value || null)} aria-label={field.label}>
      <option value="">{field.required ? 'Choose…' : 'None'}</option>
      {cmds.map((c) => (
        <option key={c.id} value={c.id}>
          {c.label}
        </option>
      ))}
    </select>
  );
}

function ReadingsField({ field, value, onChange, names, onName }) {
  const { entities, entitiesById, kindsById } = useHub();
  const rows = Array.isArray(value) ? value : [];
  const withProps = (entities || []).filter((e) => (e.properties || []).length);
  const set = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="tf-readings">
      {rows.map((r, i) => {
        const e = entitiesById[r.entity];
        return (
          <div key={i} className="tf-reading">
            <select className="select select--sm" value={r.entity || ''} aria-label={`Reading ${i + 1}: device`} onChange={(ev) => {
              const ne = entitiesById[ev.target.value];
              set(i, { entity: ev.target.value, property: ne && (ne.properties || [])[0] ? ne.properties[0].id : '' });
            }}>
              <option value="">Device…</option>
              {groups(withProps, kindsById).map(([name, list]) => (
                <optgroup key={name} label={name}>
                  {list.map((d) => (
                    <option key={d.id} value={d.id}>
                      {d.name}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
            <select className="select select--sm" value={r.property || ''} aria-label={`Reading ${i + 1}: value`} onChange={(ev) => set(i, { property: ev.target.value })} disabled={!e}>
              {((e && e.properties) || []).map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
            <button type="button" className="icon-btn" aria-label={`Remove reading ${i + 1}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
              <Icon name="x" size={14} />
            </button>
            {e && onName ? (
              <span className="tf-showas-line">
                <span className="tf-showas-label">Show as</span>
                <ShowAs e={e} names={names} onName={onName} />
              </span>
            ) : null}
          </div>
        );
      })}
      <button type="button" className="add-dev" onClick={() => onChange([...rows, { entity: '', property: '' }])}>
        <Icon name="plus" size={14} /> Add a reading
      </button>
      {field.required && !rows.length ? <div className="help">Add at least one.</div> : null}
    </div>
  );
}

/** People from the household, ticked; none ticked = everyone. */
function PeopleField({ field, value, onChange }) {
  const people = useApi('api/people');
  const picked = Array.isArray(value) ? value : [];
  const list = people.data || [];
  const toggle = (id, on) => onChange(on ? [...picked, id] : picked.filter((x) => x !== id));
  if (people.data === undefined) return <div className="help">{people.error ? "Couldn't load the household." : 'Loading…'}</div>;
  if (!list.length) return <div className="help">Nobody in the household yet.</div>;
  return (
    <div className="tf-people" role="group" aria-label={field.label}>
      {list.map((p) => (
        <Check key={p.id} checked={picked.includes(p.id)} onChange={(on) => toggle(p.id, on)}>
          {p.name}
        </Check>
      ))}
      <div className="help">{picked.filter((id) => list.some((p) => p.id === id)).length ? 'Only those ticked.' : 'Nobody ticked: everyone.'}</div>
    </div>
  );
}

/** One field of a tile's config. */
export function TileField({ field: raw, config, onChange, tileType, names, onName, pages }) {
  // A field without a label gets one from its key ("textSize" → "Text size").
  const field = raw.label ? raw : { ...raw, label: (raw.key.charAt(0).toUpperCase() + raw.key.slice(1)).replace(/([a-z])([A-Z])/g, (m, a, b) => a + ' ' + b.toLowerCase()) };
  const value = config[field.key];
  const set = (v) => onChange({ ...config, [field.key]: v });
  let input;
  if (field.type === 'entities') input = <EntitiesField field={field} value={value} onChange={set} orderable={tileType === 'camera'} names={names} onName={onName} />;
  else if (field.type === 'entity')
    input = (
      <EntityField
        field={field}
        value={value}
        names={names}
        onName={onName}
        onChange={(v) => onChange({ ...config, [field.key]: v, ...(v !== value ? { command: null, modeCommand: null } : {}) })}
      />
    );
  else if (field.type === 'command') input = <CommandField field={field} value={value} onChange={set} config={config} />;
  else if (field.type === 'readings') input = <ReadingsField field={field} value={value} onChange={set} names={names} onName={onName} />;
  else if (field.type === 'people') input = <PeopleField field={field} value={value} onChange={set} />;
  else if (field.type === 'page')
    input = (
      <select className="select" value={value || ''} aria-label={field.label} onChange={(e) => set(e.target.value || null)}>
        <option value="">None</option>
        {(pages || []).map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
    );
  else if (field.type === 'number' && field.slider) {
    const v = typeof value === 'number' ? value : field.default ?? field.min ?? 0;
    input = (
      <span className="tf-slider">
        <input type="range" min={field.min ?? 0} max={field.max ?? 100} step={field.step || 1} value={v} aria-label={field.label} aria-valuetext={`${v}${field.unit || ''}`} onChange={(e) => set(Number(e.target.value))} />
        <span className="tf-slider-value">
          {v}
          {field.unit || ''}
        </span>
        {field.default !== undefined && v !== field.default ? (
          <button type="button" className="btn-link" onClick={() => set(field.default)}>
            Reset
          </button>
        ) : null}
      </span>
    );
  } else if (field.type === 'number')
    input = <input className="input" type="number" min={field.min} max={field.max} value={value ?? ''} aria-label={field.label} onChange={(e) => set(e.target.value === '' ? null : Math.max(field.min ?? -Infinity, Math.min(field.max ?? Infinity, Number(e.target.value))))} />;
  else if (field.type === 'select')
    input = (
      <select className="select" value={value ?? field.default ?? ''} aria-label={field.label} onChange={(e) => set(e.target.value)}>
        {(field.options || []).map((o) => (
          <option key={o.value} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  else input = <input className="input" value={value ?? ''} aria-label={field.label} onChange={(e) => set(e.target.value)} />;
  return (
    <div className="field tf-field">
      <span className="field-label">
        {field.label}
        {field.required ? <span className="tf-req"> · required</span> : null}
      </span>
      {input}
    </div>
  );
}
