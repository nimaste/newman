// Connections: a rail of installed connections, the selected one with tabs
// (Devices / Settings / Activity), and "Add connection" as a modal picker.
// The page fits the window; only the pane contents scroll.

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { navigate, useActivityFeed, useApi, useHub } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Check, Empty, ErrorBox, Loading, Modal, MoreMenu, Segmented, Switch, Tabs } from '../components/Controls.jsx';
import { DeviceTileGroups } from '../components/DeviceTiles.jsx';
import { useDeviceActions } from '../components/DeviceControls.jsx';
import { AlertList } from '../components/AlertCard.jsx';
import { ActivityItem } from '../components/Camera.jsx';
import { Icon } from '../icons.jsx';
import { AddByCodeDialog, PairedDevices, WebhookAddresses } from '../components/ConnectionExtras.jsx';
import { cleanDetail, plural } from '../format.js';
import { roomOf } from '../model.js';

const MASK = '••••';
const STATUS_LABEL = { connected: 'Connected', connecting: 'Connecting', pairing: 'Pairing', error: 'Not working', disabled: 'Paused' };
const DOT = { connected: 'dot--teal', connecting: 'dot--connecting', pairing: 'dot--pairing', error: 'dot--danger', disabled: '' };
const RANK = { error: 0, pairing: 1, connecting: 2, disabled: 4 };

/** Detail plus device count, unless the hub's detail already mentions devices. */
function withCount(i) {
  const count = plural(i.entityCount || 0, 'device');
  const detail = cleanDetail(i.detail);
  if (!detail) return count;
  return /\bdevices?\b/i.test(detail) ? detail : detail + ' · ' + count;
}

/** What a connection brings, from its `provides` kind ids and the kinds' plural names in /api/meta. */
function kindNames(item, kindsById) {
  return ((item && item.provides) || []).map((id) => kindsById[id] && (kindsById[id].plural || kindsById[id].name)).filter(Boolean);
}
function providesText(item, kindsById) {
  const names = kindNames(item, kindsById);
  if (names.length <= 4) return names.join(' · ');
  return `Many kinds: ${names.slice(0, 4).map((n) => n.toLowerCase()).join(', ')}…`;
}
/** Words a search can match for a connection: its kinds' names and ids too ("plug", "leak", "vacuum"). */
function kindWords(item, kindsById) {
  return ((item && item.provides) || []).map((id) => [id, kindsById[id] && kindsById[id].name, kindsById[id] && kindsById[id].plural].filter(Boolean).join(' ')).join(' ');
}
/* ---------------- Generated forms ---------------- */

/** Fields for an installed connection whose type isn't in the catalog: infer from its config. */
function inferFields(config) {
  return Object.keys(config || {}).map((key) => {
    const v = config[key];
    return { key, label: key, type: typeof v === 'boolean' ? 'boolean' : typeof v === 'number' ? 'number' : v === MASK ? 'password' : 'text' };
  });
}

function initialValues(fields, config) {
  const out = {};
  fields.forEach((f) => {
    const v = config ? config[f.key] : undefined;
    if (f.type === 'boolean') out[f.key] = v !== undefined ? !!v : !!f.default;
    else if (f.type === 'multiselect') out[f.key] = Array.isArray(v) ? v.map(String) : Array.isArray(f.default) ? f.default.map(String) : [];
    else if (f.type === 'devices') out[f.key] = Array.isArray(v) ? v.map((x) => ({ ...x })) : [];
    else if (f.type === 'select' && (v === undefined || v === null)) out[f.key] = f.default != null ? String(f.default) : f.options && f.options[0] ? String(f.options[0].value) : '';
    else if (v !== undefined && v !== null) out[f.key] = String(v);
    else out[f.key] = f.default !== undefined && f.default !== null ? String(f.default) : '';
  });
  return out;
}

/** Field values back to config; keys the form doesn't show (e.g. min_score) are kept from the saved config. */
function buildConfig(fields, values, base) {
  const out = { ...(base || {}) };
  fields.forEach((f) => {
    const v = values[f.key];
    if (f.type === 'boolean') out[f.key] = !!v;
    else if (f.type === 'number') out[f.key] = v === '' || v === undefined ? null : Number(v);
    else if (f.type === 'multiselect') out[f.key] = Array.isArray(v) ? v : [];
    else if (f.type === 'devices') out[f.key] = (Array.isArray(v) ? v : []).filter((x) => itemStarted(f, x)).map((x) => itemConfig(f, x));
    else out[f.key] = v === undefined ? '' : v;
  });
  return out;
}

/** A row of a "devices" list the person has started filling in (blank rows are dropped on save). */
function itemStarted(field, x) {
  const sub = field.itemFields || [];
  if (!sub.length || sub.some((s) => s.key === 'host')) return !!String(x.host || '').trim();
  return sub.some((s) => s.type !== 'select' && s.type !== 'boolean' && String(x[s.key] ?? '').trim());
}

/** A row with every required sub-field filled. */
function itemReady(field, x) {
  const sub = field.itemFields || [];
  if (!sub.length) return !!String(x.host || '').trim();
  return sub.every((s) => !s.required || String(x[s.key] ?? '').trim());
}

/** Required fields still empty (a list needs at least one complete row). */
function missingFields(fields, values) {
  return fields.filter((f) => {
    if (!f.required || f.type === 'boolean' || f.type === 'multiselect') return false;
    if (f.type === 'devices') return !(Array.isArray(values[f.key]) ? values[f.key] : []).some((x) => itemReady(f, x));
    return !String(values[f.key] ?? '').trim();
  });
}

const isColour = (v) => /^#[0-9a-f]{3,8}$/i.test(String(v || ''));

/** One row of a "devices" field, with only the declared sub-fields. */
function itemConfig(field, x) {
  const out = {};
  (field.itemFields || []).forEach((s) => {
    const v = x[s.key];
    out[s.key] = s.type === 'number' ? (v === '' || v === undefined || v === null ? null : Number(v)) : v === undefined || v === null ? (s.default ?? '') : String(v).trim();
  });
  return out;
}

function newItem(field, host = '', name = '') {
  const out = {};
  (field.itemFields || []).forEach((s) => (out[s.key] = s.default ?? ''));
  if ('host' in out || !(field.itemFields || []).length) out.host = host;
  if ('name' in out) out.name = name;
  return out;
}

/**
 * "devices" field: one row per camera / plug / speaker…, plus — for `dynamic` fields — "Scan my
 * network": the hub looks (a few seconds) and lists what answered, to add in one click. Only on click.
 */
function DevicesField({ field, value, onChange, disabled, scanId, autoScan }) {
  const rows = Array.isArray(value) ? value : [];
  const sub = field.itemFields || [];
  const byAddress = !sub.length || sub.some((s) => s.key === 'host');
  const hosts = rows.map((r) => String(r.host || '').trim());
  const [scan, setScan] = useState({ state: 'idle', items: [] });
  const runScan = useCallback(async () => {
    if (!scanId) return;
    setScan({ state: 'busy', items: [] });
    try {
      const res = await api.get('api/integrations/' + enc(scanId) + '/options', { quiet: true });
      setScan({ state: 'done', items: (res && res[field.key]) || [] });
    } catch (e) {
      setScan({ state: 'error', items: [], error: e.message || 'The scan didn’t work.' });
    }
  }, [scanId, field.key]);
  const autoRan = useRef(false);
  useEffect(() => {
    if (autoScan && field.dynamic && !autoRan.current) {
      autoRan.current = true;
      runScan();
    }
  }, [autoScan, field.dynamic, runScan]);
  const isAdded = (o) => o.group === 'Already added' || hosts.includes(String(o.value));
  const found = scan.items.filter((o) => !isAdded(o));
  const added = scan.items.filter(isAdded);
  const set = (i, patch) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const noun = String(field.label || 'item').toLowerCase();
  const one = byAddress ? 'device' : noun.replace(/s$/, '');
  // Column widths from the sub-fields; a link gets a line of its own under the rest, colour pickers stay small.
  const isLink = (s) => s.key === 'url' || /^https?:|link/i.test(String(s.placeholder || '') + ' ' + String(s.label || ''));
  const stacked = sub.some(isLink);
  const cols =
    sub
      .filter((s) => !(stacked && isLink(s)))
      .map((s) => (s.type === 'select' ? ((s.options || []).every((o) => isColour(o.value)) ? '150px' : 'minmax(0, 1.4fr)') : 'minmax(0, 1fr)'))
      .join(' ') + ' 36px';
  return (
    <fieldset className="field devfield" disabled={disabled}>
      <legend className="field-label">
        {field.label}
        {field.required ? <span className="req"> *</span> : null}
      </legend>
      {field.help ? <div className="help">{field.help}</div> : null}
      <div className="devrows">
        {!rows.length ? <div className="muted small">{byAddress ? 'No devices yet — add one below.' : `No ${noun} yet — add one below.`}</div> : null}
        {rows.map((r, i) => (
          <div key={i} className={'devrow' + (stacked ? ' devrow--stacked' : '')} style={{ '--devcols': cols }}>
            {sub.map((s) => {
              if (s.type === 'select') {
                const colours = (s.options || []).every((o) => isColour(o.value));
                const v = r[s.key] ?? s.default ?? '';
                return (
                  <label key={s.key} className="devcell">
                    <span className="devcell-label">{s.label}</span>
                    <span className={colours ? 'devcell-colour' : undefined}>
                      {colours ? <span className="devcell-swatch" style={{ background: v }} aria-hidden="true" /> : null}
                      <select className="select select--sm" value={v} aria-label={`${s.label} for ${r.name || one + ' ' + (i + 1)}`} onChange={(e) => set(i, { [s.key]: e.target.value })}>
                        {(s.options || []).map((o) => (
                          <option key={o.value} value={o.value}>
                            {o.label}
                          </option>
                        ))}
                      </select>
                    </span>
                  </label>
                );
              }
              const secret = s.type === 'password';
              const v = r[s.key] ?? '';
              return (
                <label key={s.key} className={'devcell' + (s.key === 'host' ? ' devcell--host' : '') + (stacked && isLink(s) ? ' devcell--line' : '')}>
                  <span className="devcell-label">
                    {s.label}
                    {s.required ? <span className="req"> *</span> : null}
                    {secret && v === MASK ? <span className="devcell-kept"> · saved</span> : null}
                  </span>
                  <input
                    className="input input--sm"
                    type={secret ? 'password' : s.type === 'number' ? 'number' : 'text'}
                    autoComplete={secret ? 'new-password' : 'off'}
                    value={v}
                    placeholder={s.placeholder || (s.required ? '' : 'optional')}
                    title={secret && v === MASK ? 'Saved. Leave it as is to keep it, or paste a new one.' : undefined}
                    onFocus={(e) => secret && v === MASK && e.target.select()}
                    onChange={(e) => set(i, { [s.key]: e.target.value })}
                  />
                </label>
              );
            })}
            <button type="button" className="icon-btn devrow-x" aria-label={`Remove ${r.name || r.host || one}`} onClick={() => onChange(rows.filter((_, j) => j !== i))}>
              <Icon name="x" size={16} />
            </button>
          </div>
        ))}
      </div>
      {field.dynamic && scan.state !== 'idle' ? (
        <div className="devfound" aria-live="polite">
          <div className="devfound-title">{scan.state === 'busy' ? 'Looking on your network…' : 'Found on your network'}</div>
          {scan.state === 'busy' ? <div className="muted small">This takes a few seconds.</div> : null}
          {scan.state === 'error' ? <div className="small amber">{scan.error}</div> : null}
          {scan.state === 'done' && !scan.items.length ? <div className="muted small">Nothing answered. Check they're switched on and on the same network, or add one by address.</div> : null}
          {found.map((o) => (
            <div key={o.value} className="devfound-row">
              <span>{o.label}</span>
              <button type="button" className="btn btn-sm2" onClick={() => onChange([...rows, newItem(field, String(o.value), String(o.label).replace(/\s*\(.*\)$/, ''))])}>
                <Icon name="plus" size={14} /> Add
              </button>
            </div>
          ))}
          {added.map((o) => (
            <div key={o.value} className="devfound-row devfound-row--added">
              <span>{o.label}</span>
              <span className="muted small">
                <Icon name="check" size={13} /> Added
              </span>
            </div>
          ))}
        </div>
      ) : null}
      <div className="devfield-actions">
        {field.dynamic ? (
          <button type="button" className="btn btn-sm2" disabled={!scanId || scan.state === 'busy'} onClick={runScan} title={scanId ? undefined : 'Connect first, then scan from its Settings'}>
            {scan.state === 'busy' ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name="refresh" size={14} />}
            {scan.state === 'busy' ? 'Scanning…' : scan.state === 'idle' ? 'Scan my network' : 'Scan again'}
          </button>
        ) : null}
        <button type="button" className="btn btn-sm2 add-dev-btn" onClick={() => onChange([...rows, newItem(field)])}>
          <Icon name="plus" size={14} /> {byAddress ? 'Add by address' : `Add ${one}`}
        </button>
      </div>
      {field.dynamic && !scanId ? <div className="help">Connect first; then “Scan my network” from its Settings finds them for you.</div> : null}
    </fieldset>
  );
}

/** Rarely needed settings are folded away under "More settings" (the hub marks them advanced). */
function isAdvanced(f) {
  if (f.advanced === true) return true;
  if (f.advanced === false || f.required) return false;
  // Older hubs don't mark fields: fall back to a guess.
  return /^mqtt_|(^|_)port$|https|birth|prefix|cooldown|channel|poll|travel|^mfa$|min_score/.test(f.key);
}

function MultiSelect({ field, value, onChange, options, disabled }) {
  const selected = Array.isArray(value) ? value : [];
  if (!options) {
    return (
      <div className="field">
        <div className="field-label">{field.label}</div>
        <div className="note">{options === undefined ? 'Loading choices…' : field.dynamic ? `Choose ${field.label.toLowerCase()} after connecting.` : 'No choices available.'}</div>
        {field.help ? <div className="help">{field.help}</div> : null}
      </div>
    );
  }
  const groups = [];
  options.forEach((o) => {
    const g = o.group || '';
    let grp = groups.find((x) => x.name === g);
    if (!grp) groups.push((grp = { name: g, items: [] }));
    grp.items.push(o);
  });
  const toggle = (v) => onChange(selected.includes(v) ? selected.filter((x) => x !== v) : [...selected, v]);
  return (
    <fieldset className="field multi" disabled={disabled}>
      <legend className="field-label">{field.label}</legend>
      <div className="multi-tools">
        <span className="muted">{selected.length ? `${selected.length} of ${options.length} ticked` : 'None ticked'}</span>
        <button type="button" className="btn-link" onClick={() => onChange(options.map((o) => String(o.value)))}>
          All
        </button>
        <button type="button" className="btn-link" onClick={() => onChange([])}>
          None
        </button>
      </div>
      <div className={'multi-grid' + (groups.some((g) => g.name) ? ' multi-grid--groups' : '')}>
        {groups.some((g) => g.name)
          ? groups.map((g) => (
              <div key={g.name || '_'} className="multi-group">
                {g.name ? <div className="multi-group-name">{g.name}</div> : null}
                {g.items.map((o) => (
                  <Check key={o.value} checked={selected.includes(String(o.value))} onChange={() => toggle(String(o.value))}>
                    {o.label}
                  </Check>
                ))}
              </div>
            ))
          : options.map((o) => (
              <Check key={o.value} checked={selected.includes(String(o.value))} onChange={() => toggle(String(o.value))}>
                {o.label}
              </Check>
            ))}
      </div>
      {field.help ? <div className="help">{field.help}</div> : null}
    </fieldset>
  );
}

function Field({ field, value, onChange, disabled, readOnly, options, scanId, autoScan }) {
  if (field.type === 'devices') return <DevicesField field={field} value={value} onChange={onChange} disabled={disabled} scanId={scanId} autoScan={autoScan} />;
  if (field.type === 'multiselect') return <MultiSelect field={field} value={value} onChange={onChange} options={options} disabled={disabled} />;
  const id = 'f-' + field.key;
  const help = field.help ? (
    <div className="help" id={id + '-help'}>
      {field.help}
    </div>
  ) : null;
  if (field.type === 'boolean') {
    return (
      <div className="field">
        <Switch checked={!!value} onChange={onChange} disabled={disabled} label={field.label} />
        {help}
      </div>
    );
  }
  if (field.type === 'select' && (options || []).length <= 3 && (options || []).every((o) => String(o.label).length < 34)) {
    return (
      <div className="field">
        <span className="field-label">{field.label}</span>
        <Segmented label={field.label} value={value} onChange={onChange} options={(options || []).map((o) => ({ id: String(o.value), label: o.label }))} />
        {help}
      </div>
    );
  }
  const common = { id, disabled, readOnly, required: !!field.required, placeholder: field.placeholder || '', 'aria-describedby': field.help ? id + '-help' : undefined };
  return (
    <div className="field">
      <label className="field-label" htmlFor={id}>
        {field.label}
        {field.required ? <span className="req"> *</span> : null}
      </label>
      {field.type === 'select' ? (
        <select className="select" {...common} value={value} onChange={(e) => onChange(e.target.value)}>
          {(options || []).map((o) => (
            <option key={o.value} value={String(o.value)}>
              {o.label}
            </option>
          ))}
        </select>
      ) : field.type === 'textarea' ? (
        <textarea className="textarea" {...common} value={value} onChange={(e) => onChange(e.target.value)} />
      ) : (
        <input
          className="input"
          {...common}
          type={field.type === 'password' ? 'password' : field.type === 'number' ? 'number' : 'text'}
          autoComplete={field.type === 'password' ? 'new-password' : 'off'}
          value={value}
          onFocus={(e) => field.type === 'password' && value === MASK && e.target.select()}
          onChange={(e) => onChange(e.target.value)}
        />
      )}
      {readOnly ? <div className="help">Filled in from the device Newman found.</div> : null}
      {field.type === 'password' && value === MASK ? <div className="help">Saved. Leave it as is to keep it, or type a new one.</div> : null}
      {help}
    </div>
  );
}

/** Two columns: simple fields left (Advanced folded), long lists right. */
function FieldColumns({ fields, values, setValue, optionsFor, disabled, lockedKeys = [], nameField, scanId, autoScan }) {
  const wide = fields.filter((f) => f.type === 'multiselect' || f.type === 'textarea' || f.type === 'devices');
  const simple = fields.filter((f) => !wide.includes(f) && !isAdvanced(f));
  const advanced = fields.filter((f) => !wide.includes(f) && isAdvanced(f));
  const render = (f) => (
    <Field key={f.key} field={f} options={optionsFor(f)} value={values[f.key] ?? ''} disabled={disabled} readOnly={lockedKeys.includes(f.key)} onChange={(v) => setValue(f.key, v)} scanId={scanId} autoScan={autoScan} />
  );
  return (
    <div className={'form-cols' + (wide.length ? '' : ' form-cols--one')}>
      <div className="form-col">
        {nameField}
        {simple.map(render)}
        {advanced.length ? (
          <details className="advanced">
            <summary>More settings</summary>
            <div className="form-col">{advanced.map(render)}</div>
          </details>
        ) : null}
      </div>
      {wide.length ? <div className="form-col">{wide.map(render)}</div> : null}
    </div>
  );
}

function useFieldOptions(installed, fields) {
  // Lists of devices scan only when asked (DevicesField); other dynamic choices load with the form.
  const dynamic = !!installed && fields.some((f) => f.dynamic && f.type !== 'devices');
  const dyn = useApi(dynamic ? 'api/integrations/' + enc(installed.id) + '/options' : null);
  return (f) => {
    if (!f.dynamic) return f.options || [];
    if (!installed) return null;
    if (dyn.data === undefined) return dyn.error ? [] : undefined;
    return dyn.data[f.key] || [];
  };
}

/**
 * What a waiting ("pairing") connection needs from you, from its fields — never its type:
 * a `code` field → paste the code it sent; a scannable device list → scan the network;
 * a detail that points at the settings → open them. Otherwise it waits for the device (a button press).
 */
function pairingAction(fields, detail) {
  const fs = fields || [];
  if (fs.some((f) => f.key === 'code'))
    return { head: 'Waiting for the code', label: 'Enter the code', icon: 'key', focus: 'code', hint: 'Paste it into Settings and save; Newman signs in and forgets the code.' };
  if (fs.some((f) => f.type === 'devices' && f.dynamic))
    return { head: 'Waiting for your devices', label: 'Scan my network', icon: 'refresh', scan: true, hint: 'Newman looks for a few seconds; add the ones you want and save.' };
  if (fs.length && /settings/i.test(detail || '')) return { head: 'Waiting for setup', label: 'Open settings', icon: 'sliders', hint: '' };
  return null;
}
// The action from the add-connection modal: open that connection's settings ready for it.
const openSettingsFor = { id: null, action: null };

/** Big amber instruction while a connection waits for a button press, a code or its settings. */
function PairingPanel({ integration, action, onAction }) {
  return (
    <div className="pairing-box" role="status" aria-live="polite">
      <div className="pairing-head">
        <span className="dot dot--pairing dot--lg" aria-hidden="true" />
        {action ? action.head : 'Waiting for pairing'}
      </div>
      <div className="pairing-text">{cleanDetail(integration.detail) || 'Follow the instructions on the device.'}</div>
      {action && onAction ? (
        <div className="pairing-actions">
          <button type="button" className="btn btn-primary" onClick={onAction}>
            <Icon name={action.icon} size={16} /> {action.label}
          </button>
          {action.hint ? <span className="help amber">{action.hint}</span> : null}
        </div>
      ) : (
        <div className="help amber">This updates by itself as soon as the device answers.</div>
      )}
    </div>
  );
}

/* ---------------- Selected connection ---------------- */

function SettingsTab({ installed, entry, onSaved, focusKey, scanKey }) {
  const fields = (entry && entry.fields) || inferFields(installed.config);
  const start = useMemo(() => initialValues(fields, installed.config), [installed.config]); // eslint-disable-line react-hooks/exhaustive-deps
  const [values, setValues] = useState(start);
  const [name, setName] = useState(installed.name);
  const [busy, setBusy] = useState(false);
  const optionsFor = useFieldOptions(installed, fields);
  const dirty = name !== installed.name || JSON.stringify(values) !== JSON.stringify(start);
  const missing = missingFields(fields, values);
  // Opened with "Enter the code": straight to that field.
  useEffect(() => {
    if (!focusKey) return;
    const el = document.getElementById('f-' + focusKey.split(':')[0]);
    if (el) {
      el.scrollIntoView({ block: 'center' });
      el.focus();
    }
  }, [focusKey]);

  const save = async () => {
    setBusy(true);
    try {
      await api.patch('api/integrations/' + enc(installed.id), { name: name.trim() || installed.name, config: buildConfig(fields, values, installed.config) });
      toast(`Saved ${name || installed.name}`, 'success');
      onSaved();
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div className="pane-body">
        <FieldColumns
          fields={fields}
          values={values}
          setValue={(k, v) => setValues((s) => ({ ...s, [k]: v }))}
          optionsFor={optionsFor}
          disabled={busy}
          scanId={installed.id}
          autoScan={!!scanKey}
          nameField={
            <div className="field">
              <label className="field-label" htmlFor="conn-name">
                Connection name
              </label>
              <input id="conn-name" className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={busy} />
            </div>
          }
        />
      </div>
      <div className="pane-foot">
        {missing.length ? <span className="help amber" style={{ marginRight: 'auto' }}>Required: {missing.map((f) => f.label).join(', ')}</span> : null}
        <button
          type="button"
          className="btn"
          disabled={!dirty || busy}
          onClick={() => {
            setValues(start);
            setName(installed.name);
          }}
        >
          Discard
        </button>
        <button type="button" className="btn btn-primary" disabled={!dirty || busy || missing.length > 0} onClick={save}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </>
  );
}

function DevicesTab({ devices }) {
  const { kinds } = useHub();
  const { run, busy } = useDeviceActions();
  const [type, setType] = useState('');
  const [q, setQ] = useState('');
  const types = kinds.filter((k) => devices.some((e) => e.kind === k.id));
  const needle = q.trim().toLowerCase();
  const shown = devices.filter(
    (e) => (!type || e.kind === type) && (!needle || e.name.toLowerCase().includes(needle) || roomOf(e).toLowerCase().includes(needle)),
  );
  return (
    <>
      <div className="pane-tools">
        {types.length > 1 ? (
          <select className="select select--sm2" value={type} onChange={(e) => setType(e.target.value)} aria-label="Type">
            <option value="">All types ({devices.length})</option>
            {types.map((k) => (
              <option key={k.id} value={k.id}>
                {k.plural || k.name} ({devices.filter((e) => e.kind === k.id).length})
              </option>
            ))}
          </select>
        ) : null}
        <input className="input input--sm2" type="search" placeholder="Search devices…" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search devices" />
      </div>
      <div className="pane-body">
        <DeviceTileGroups entities={shown} run={run} busy={busy} emptyText={devices.length ? 'Nothing matches.' : "This connection hasn't reported any devices yet."} />
      </div>
    </>
  );
}

function ActivityTab({ installed, devices }) {
  // A connection that keeps alerts (hasAlerts) shows them; others their devices' recent activity.
  const hasAlerts = !!installed.hasAlerts;
  const events = useActivityFeed(300, { enabled: !hasAlerts });
  if (hasAlerts) {
    return (
      <div className="pane-body">
        <AlertList path={'api/integrations/' + enc(installed.id) + '/alerts'} grid />
      </div>
    );
  }
  const ids = new Set(devices.map((e) => e.id));
  const mine = (events.data || []).filter((x) => ids.has(x.entityId)).slice(0, 40);
  return (
    <div className="pane-body">
      {events.data === undefined ? (
        events.error ? <ErrorBox error={events.error} onRetry={events.reload} /> : <Loading />
      ) : !mine.length ? (
        <Empty title="Nothing recent">Activity from this connection's devices shows up here.</Empty>
      ) : (
        mine.map((x) => <ActivityItem key={x.id} event={x} />)
      )}
    </div>
  );
}

function ConnectionDetail({ installed, entry, onChanged }) {
  const { entities } = useHub();
  // Family accounts can look, not change: no pause/remove, no settings.
  const { isAdmin } = useAuth();
  const confirm = useConfirm();
  const pending = openSettingsFor.id === installed.id ? openSettingsFor.action : null;
  const [tab, setTab] = useState(() => (pending && isAdmin ? 'settings' : 'devices'));
  const [focusKey, setFocusKey] = useState(() => (pending && pending.focus ? pending.focus + ':0' : null));
  const [scanKey, setScanKey] = useState(() => (pending && pending.scan ? 'scan:0' : null));
  const [busy, setBusy] = useState(false);
  const [reconnecting, setReconnecting] = useState(false);
  const [pairOpen, setPairOpen] = useState(false);
  const devices = (entities || []).filter((e) => e.integration === installed.id);
  const status = installed.status;
  const enabled = installed.enabled !== undefined ? installed.enabled : status !== 'disabled';
  const fields = (entry && entry.fields) || [];
  const action = status === 'pairing' && isAdmin ? pairingAction(fields, installed.detail) : null;
  const doAction = action
    ? () => {
        setTab('settings');
        if (action.focus) setFocusKey(action.focus + ':' + Date.now());
        if (action.scan) setScanKey('scan:' + Date.now());
      }
    : null;
  // A connection that adds devices by a setup code (its catalog entry says how): an "Add a device" dialog,
  // and a tab listing what it paired, each removable.
  const addSpec = entry && entry.addByCode;
  const paired = isAdmin && installed.adminDetails && Array.isArray(installed.adminDetails.pairedDevices) ? installed.adminDetails.pairedDevices : null;
  const reload = useCallback(() => onChanged(installed.id), [onChanged, installed.id]);
  // Webhook addresses carry their secret: the hub sends them to admin sessions only.
  const hooks = isAdmin && installed.adminDetails && Array.isArray(installed.adminDetails.addresses) ? installed.adminDetails.addresses.filter((h) => h && h.path) : [];

  useEffect(() => {
    if (openSettingsFor.id === installed.id) openSettingsFor.id = null;
    else setTab('devices');
  }, [installed.id]);

  const setEnabled = async (v) => {
    setBusy(true);
    try {
      await api.patch('api/integrations/' + enc(installed.id), { enabled: v });
      toast(`${installed.name} ${v ? 'resumed' : 'paused'}`, 'success');
      onChanged(installed.id);
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };
  const reconnect = async () => {
    setReconnecting(true);
    try {
      const res = await api.post('api/integrations/' + enc(installed.id) + '/reconnect');
      const st = res && res.status;
      if (st === 'connected') toast(`${installed.name} reconnected`, 'success');
      else if (st === 'error') toast(`${installed.name} couldn't reconnect${res.detail ? ': ' + cleanDetail(res.detail) : ''}`, 'error');
      else toast(`${installed.name} is reconnecting…`, 'info');
      onChanged(installed.id);
    } catch {
      /* toast shown */
    } finally {
      setReconnecting(false);
    }
  };
  const remove = async () => {
    const ok = await confirm({
      title: `Remove ${installed.name}?`,
      body: `Its ${plural(installed.entityCount || 0, 'device')} disappear from Newman, and automations that use them stop working until they come back.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del('api/integrations/' + enc(installed.id));
      toast(`Removed ${installed.name}`, 'success');
      onChanged(null);
    } catch {
      /* toast shown */
    }
  };

  return (
    <section className="pane" aria-labelledby="conn-title">
      <div className="conn-head">
        <div className="conn-head-text">
          <h2 id="conn-title" className="conn-title">
            {installed.name}
          </h2>
          <div className="conn-status">
            <span className={'dot ' + (DOT[status] || '')} aria-hidden="true" />
            <strong className={'int-status--' + status}>{STATUS_LABEL[status] || status}</strong>
            <span className="muted"> · {withCount(installed)}</span>
          </div>
        </div>
        {isAdmin ? (
          <>
            {addSpec ? (
              <button type="button" className="btn btn-sm2 btn-primary" onClick={() => setPairOpen(true)}>
                <Icon name="plus" size={14} /> Add a {entry && entry.name ? entry.name : ''} device
              </button>
            ) : null}
            <button type="button" className="btn btn-sm2" disabled={busy || reconnecting || !enabled} onClick={reconnect} aria-busy={reconnecting}>
              {reconnecting ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name="refresh" size={14} />}
              {reconnecting ? 'Reconnecting…' : 'Reconnect'}
            </button>
            <button type="button" className="btn btn-sm2" disabled={busy || reconnecting || status === 'pairing'} onClick={() => setEnabled(!enabled)}>
              {enabled ? 'Pause' : 'Resume'}
            </button>
            <MoreMenu
              items={[
                { label: reconnecting ? 'Reconnecting…' : 'Reconnect', disabled: busy || reconnecting || !enabled, onClick: reconnect },
                { label: 'Remove connection', danger: true, onClick: remove },
              ]}
              label={`More actions for ${installed.name}`}
            />
          </>
        ) : null}
      </div>
      {status === 'pairing' ? (
        <div className="conn-banner">
          <PairingPanel integration={installed} action={action} onAction={doAction} />
        </div>
      ) : status === 'error' && installed.detail ? (
        <div className="conn-banner">
          <div className="error-box" role="alert">
            <span>{cleanDetail(installed.detail)}</span>
          </div>
        </div>
      ) : null}
      {hooks.length ? (
        <div className="conn-banner">
          <WebhookAddresses hooks={hooks} />
        </div>
      ) : null}
      {pairOpen && addSpec ? <AddByCodeDialog installed={installed} spec={addSpec} title={`Add a ${entry.name} device`} onClose={() => setPairOpen(false)} onChanged={reload} /> : null}
      <div className="conn-tabs">
        <Tabs
          label="Connection sections"
          value={tab}
          onChange={setTab}
          tabs={[
            { id: 'devices', label: `Devices (${devices.length})` },
            ...(paired ? [{ id: 'paired', label: `Paired (${paired.length})` }] : []),
            ...(isAdmin ? [{ id: 'settings', label: 'Settings' }] : []),
            { id: 'activity', label: installed.hasAlerts ? 'Alerts' : 'Activity' },
          ]}
        />
      </div>
      {tab === 'devices' ? <DevicesTab devices={devices} /> : null}
      {tab === 'paired' && paired ? (
        <div className="pane-body">
          <PairedDevices installed={installed} devices={paired} onChanged={reload} />
        </div>
      ) : null}
      {tab === 'settings' && isAdmin ? (
        <SettingsTab
          key={installed.id + JSON.stringify(installed.config) + installed.name}
          installed={installed}
          entry={entry}
          focusKey={focusKey}
          scanKey={scanKey}
          onSaved={() => (setFocusKey(null), setScanKey(null), onChanged(installed.id))}
        />
      ) : null}
      {tab === 'activity' ? <ActivityTab installed={installed} devices={devices} /> : null}
    </section>
  );
}

/* ---------------- Add connection (modal) ---------------- */

function SetupForm({ entry, found, onCreated, onBack }) {
  const fields = (entry && entry.fields) || inferFields(found && found.config);
  const [values, setValues] = useState(() => {
    const v = initialValues(fields, found && found.config);
    // A found Reolink camera: start its device list with that address.
    const dev = fields.find((f) => f.type === 'devices');
    if (dev && found && found.config && found.config.host && !(v[dev.key] || []).length) v[dev.key] = [newItem(dev, found.config.host, found.title || '')];
    return v;
  });
  const [name, setName] = useState((entry && entry.name) || (found && found.integrationName) || '');
  const [busy, setBusy] = useState(false);
  const optionsFor = useFieldOptions(null, fields);
  const hasNameField = fields.some((f) => f.key === 'name');
  const lockedKeys =
    found && found.config
      ? fields.filter((f) => /(^|_)id$/.test(f.key) && found.config[f.key] !== undefined && found.config[f.key] !== null && found.config[f.key] !== '').map((f) => f.key)
      : [];
  const missing = missingFields(fields, values);
  const comingLater = entry && entry.available === false;

  const submit = async (e) => {
    e.preventDefault();
    if (missing.length || comingLater) return;
    setBusy(true);
    try {
      const type = entry ? entry.type : found.type;
      const fallback = (entry && entry.name) || (found && found.integrationName) || type;
      const chosen = hasNameField ? String(values.name || '').trim() : name.trim();
      const res = await api.post('api/integrations', { type, name: chosen || fallback, config: buildConfig(fields, values, null) });
      toast(`Connecting ${(res && res.name) || fallback}…`, 'success');
      onCreated(res);
    } catch {
      setBusy(false);
    }
  };

  return (
    <form className="setup" onSubmit={submit} noValidate>
      <div className="setup-head">
        <button type="button" className="btn-link" onClick={onBack}>
          ← All connections
        </button>
        <div className="setup-title">{entry ? entry.name : found.title}</div>
        <div className="int-about">
          {found ? (
            <>
              Found on your network{found.host ? ' at ' + found.host : ''}. Check the details, then connect.
              {found.note ? <div className="int-note">{found.note}</div> : null}
            </>
          ) : (
            entry && entry.description
          )}
        </div>
      </div>
      <FieldColumns
        fields={fields}
        values={values}
        setValue={(k, v) => setValues((s) => ({ ...s, [k]: v }))}
        optionsFor={optionsFor}
        disabled={busy}
        lockedKeys={lockedKeys}
        nameField={
          hasNameField ? null : (
            <div className="field">
              <label className="field-label" htmlFor="setup-name">
                Name
              </label>
              <input id="setup-name" className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={busy} />
            </div>
          )
        }
      />
      <div className="setup-foot">
        {missing.length ? <span className="help amber">Required: {missing.map((f) => f.label).join(', ')}</span> : <span />}
        <button type="submit" className="btn btn-primary btn-lg" disabled={busy || missing.length > 0}>
          {busy ? 'Connecting…' : 'Connect'}
        </button>
      </div>
    </form>
  );
}

function AddConnectionModal({ catalog, found, listening, initial, installedList, categoriesById, onClose, onDone }) {
  const [step, setStep] = useState(initial || { view: 'pick' });
  const [q, setQ] = useState('');
  const needle = q.trim().toLowerCase();
  const match = (...s) => !needle || s.filter(Boolean).join(' ').toLowerCase().includes(needle);
  const byType = Object.fromEntries(catalog.map((c) => [c.type, c]));
  const { kindsById } = useHub();

  // After creating: wait for pairing (Lutron, HomeKit) to finish, then select it.
  const created = step.view === 'pairing' ? installedList.find((i) => i.id === step.id) : null;
  useEffect(() => {
    if (step.view === 'pairing' && created && created.status === 'connected') {
      toast(`${created.name} is connected`, 'success');
      onDone(created.id);
    }
  }, [step.view, created, onDone]);

  let body;
  if (step.view === 'setup') {
    body = (
      <SetupForm
        entry={step.entry}
        found={step.found}
        onBack={() => setStep({ view: 'pick' })}
        onCreated={(res) => {
          if (res && res.id && res.status && res.status !== 'connected') setStep({ view: 'pairing', id: res.id, name: res.name });
          else onDone(res && res.id);
        }}
      />
    );
  } else if (step.view === 'pairing') {
    body = (
      <div className="stack">
        {!created || created.status === 'connecting' ? (
          <Loading>Connecting to {step.name || 'the device'}…</Loading>
        ) : created.status === 'pairing' ? (
          <PairingPanel
            integration={created}
            action={pairingAction((catalog.find((c) => c.type === created.type) || {}).fields, created.detail)}
            onAction={() => {
              openSettingsFor.id = step.id;
              openSettingsFor.action = pairingAction((catalog.find((c) => c.type === created.type) || {}).fields, created.detail);
              onDone(step.id);
            }}
          />
        ) : created.status === 'error' ? (
          <div className="error-box" role="alert">
            <span>{cleanDetail(created.detail) || "It couldn't connect."} You can fix the settings on the connection's Settings tab.</span>
          </div>
        ) : null}
        <div className="setup-foot">
          <span />
          <button type="button" className="btn" onClick={() => onDone(step.id)}>
            {created && created.status === 'error' ? 'Open its settings' : 'Continue in the background'}
          </button>
        </div>
      </div>
    );
  } else {
    const foundShown = found.filter((f) => match(f.title, f.integrationName, f.host, kindWords(byType[f.type], kindsById)));
    const available = catalog.filter((c) => c.available !== false && match(c.name, c.description, kindWords(c, kindsById)));
    const later = catalog.filter((c) => c.available === false && match(c.name, c.description, kindWords(c, kindsById)));
    body = (
      <div className="stack">
        <input className="input" type="search" placeholder="Search brands (Lutron, Ring, Roborock…)" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search connections" />
        {listening === false ? <div className="note">Network discovery is off (the hub needs host networking).</div> : null}
        <div className="catalog-grid">
          {foundShown.map((f) => {
            const ok = f.available !== false && (byType[f.type] || f.config);
            return (
              <button key={'f' + f.key} type="button" className="cat-card cat-card--found" disabled={!ok} onClick={() => setStep({ view: 'setup', entry: byType[f.type] || null, found: f })}>
                <span className="cat-top">
                  <span className="cat-name">{f.title || f.integrationName}</span>
                  <span className="found-badge">{ok ? 'Found' : 'Found · coming later'}</span>
                </span>
                {providesText(byType[f.type], kindsById) ? <span className="cat-kinds">{providesText(byType[f.type], kindsById)}</span> : null}
                <span className="cat-note">{[f.host, f.integrationName].filter(Boolean).join(' · ')}</span>
                {f.note ? <span className="cat-note amber">{f.note}</span> : null}
              </button>
            );
          })}
          {available.map((c) => (
            <button key={c.type} type="button" className="cat-card" onClick={() => setStep({ view: 'setup', entry: c, found: null })}>
              <span className="cat-name">{c.name}</span>
              {providesText(c, kindsById) ? <span className="cat-kinds">{providesText(c, kindsById)}</span> : null}
              <span className="cat-note cat-desc" title={c.description || undefined}>
                {c.description || ''}
              </span>
            </button>
          ))}
          {later.map((c) => (
            <div key={c.type} className="cat-card cat-card--later" aria-disabled="true">
              <span className="cat-name">{c.name}</span>
              {providesText(c, kindsById) ? <span className="cat-kinds">{providesText(c, kindsById)}</span> : null}
              <span className="cat-note">Coming later</span>
            </div>
          ))}
          {!foundShown.length && !available.length && !later.length ? <div className="muted">Nothing matches “{q}”.</div> : null}
        </div>
      </div>
    );
  }

  return (
    <Modal title={step.view === 'pick' ? 'Add a connection' : step.view === 'pairing' ? 'Pairing' : 'Set up'} onClose={onClose} wide>
      {body}
    </Modal>
  );
}

/* ---------------- Page ---------------- */

export default function Integrations({ selected }) {
  const { categoriesById, reloadStatus, reloadEntities, status } = useHub();
  const { isAdmin } = useAuth();
  const anyPairing = !!(status && (status.integrations || []).some((i) => i.status === 'pairing'));
  const installed = useApi('api/integrations', { interval: anyPairing ? 3000 : 5000 });
  const catalog = useApi('api/integrations/catalog');
  const discovered = useApi('api/integrations/discovered', { interval: 10000 });
  const [modal, setModal] = useState(null);
  const handled = useRef(null);

  const ints = (installed.data || []).slice().sort((a, b) => (RANK[a.status] ?? 3) - (RANK[b.status] ?? 3));
  const cat = catalog.data || [];
  const byType = Object.fromEntries(cat.map((c) => [c.type, c]));
  const disc = discovered.data && typeof discovered.data === 'object' && !discovered.error ? discovered.data : null;
  const found = ((disc && disc.found) || []).filter(
    (f) => f && f.key && !ints.some((i) => i.type === f.type && f.host && i.config && (i.config.host === f.host || String(i.config.url || '').includes(f.host))),
  );

  // Old links (#/integrations/add:<type>, found:<key>) open the add dialog.
  useEffect(() => {
    if (!selected || handled.current === selected || !catalog.data) return;
    if (selected.startsWith('add:')) {
      handled.current = selected;
      const entry = byType[selected.slice(4)];
      setModal(entry ? { view: 'setup', entry, found: null } : { view: 'pick' });
    } else if (selected.startsWith('found:')) {
      handled.current = selected;
      const f = found.find((x) => x.key === selected.slice(6));
      setModal(f ? { view: 'setup', entry: byType[f.type] || null, found: f } : { view: 'pick' });
    }
  }); // eslint-disable-line react-hooks/exhaustive-deps

  if (installed.data === undefined || catalog.data === undefined) {
    const err = installed.error || catalog.error;
    return (
      <div className="app-page">
        <div className="app-head">
          <h1 className="page-title">Connections</h1>
        </div>
        {err ? <ErrorBox error={err} onRetry={() => (installed.reload(), catalog.reload())} /> : <Loading>Loading connections…</Loading>}
      </div>
    );
  }

  const selId = selected && !selected.startsWith('add:') && !selected.startsWith('found:') ? selected : ints[0] && ints[0].id;
  const sel = ints.find((i) => i.id === selId) || null;
  const pick = (id) => navigate('integrations/' + (id ? enc(id) : ''), { replace: true });
  const refresh = (id) => {
    installed.reload();
    reloadStatus();
    reloadEntities();
    if (id !== undefined) pick(id);
  };

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Connections</h1>
        {disc ? (
          found.length ? (
            <button type="button" className="status-pill status-pill--found" disabled={!isAdmin} onClick={() => setModal({ view: 'pick' })}>
              <span className="dot dot--amber" aria-hidden="true" />
              {found.length} found on your network
            </button>
          ) : (
            <span className={'status-pill' + (disc.listening === false ? ' status-pill--off' : '')}>
              <span className={'dot ' + (disc.listening === false ? '' : 'dot--teal')} aria-hidden="true" />
              {disc.listening === false ? 'Discovery is off' : 'Listening for new devices · nothing new'}
            </span>
          )
        ) : null}
        {isAdmin ? (
          <button type="button" className="btn btn-primary" onClick={() => setModal({ view: 'pick' })}>
            <Icon name="plus" size={16} /> Add connection
          </button>
        ) : null}
      </div>

      <div className="split">
        <nav className="rail pane" aria-label="Your connections">
          <div className="rail-list">
            {!ints.length ? (
              <Empty title="Nothing connected yet">Use “Add connection” to bring your devices into Newman.</Empty>
            ) : (
              ints.map((i) => (
                <button key={i.id} type="button" className="rail-row" aria-current={sel && sel.id === i.id ? 'true' : undefined} onClick={() => pick(i.id)}>
                  <span className={'dot ' + (DOT[i.status] || '')} aria-hidden="true" />
                  <span className="rail-text">
                    <span className="rail-name">{i.name}</span>
                    <span className="rail-sub">{i.status === 'connected' ? cleanDetail(i.detail) || STATUS_LABEL[i.status] : STATUS_LABEL[i.status] || i.status}</span>
                  </span>
                  <span className="rail-count">{i.entityCount || 0}</span>
                </button>
              ))
            )}
          </div>
        </nav>
        {sel ? (
          <ConnectionDetail key={sel.id} installed={sel} entry={byType[sel.type]} onChanged={(id) => refresh(id === null ? '' : id)} />
        ) : (
          <div className="pane">
            <Empty title="Pick a connection">Choose one on the left, or add a new one.</Empty>
          </div>
        )}
      </div>

      {modal ? (
        <AddConnectionModal
          catalog={cat}
          found={found}
          listening={disc ? disc.listening : undefined}
          initial={modal}
          installedList={installed.data || []}
          categoriesById={categoriesById}
          onClose={() => {
            setModal(null);
            if (selected && (selected.startsWith('add:') || selected.startsWith('found:'))) pick(selId);
          }}
          onDone={(id) => {
            setModal(null);
            refresh(id || selId);
          }}
        />
      ) : null}
    </div>
  );
}

