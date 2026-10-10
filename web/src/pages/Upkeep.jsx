// Upkeep: things the house needs now and then (change the furnace filter, salt the softener, the
// car's oil). Admins add, edit and delete; everyone can mark them done. They show on the wall's
// Today tile and To do screen, and are devices too (kind "upkeep") so automations can use them.
// Also here: Today's settings (when rain is worth a heads-up, when evening heads-ups start).

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading, Modal, MoreMenu, Switch } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';

const UNITS = ['days', 'weeks', 'months'];
const todayIso = () => {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
};
const fmtDate = (iso) => {
  if (!iso) return '';
  const d = new Date(String(iso).length === 10 ? iso + 'T12:00:00' : iso);
  return Number.isNaN(d.getTime()) ? '' : d.toLocaleDateString([], { month: 'short', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
};
const STATE = { overdue: ['Overdue', 'is-bad'], due: ['Due today', 'is-warn'], soon: ['Coming up', 'is-warn'], ok: ['OK', 'is-on'] };

function dueText(it) {
  const n = it.daysLeft;
  if (it.state === 'overdue') return typeof n === 'number' ? `${-n} ${-n === 1 ? 'day' : 'days'} late` : 'Overdue';
  if (it.state === 'due') return 'Due today';
  if (typeof n === 'number' && n <= 1 && n >= 0) return 'Due tomorrow';
  if (typeof n === 'number' && n > 1 && n <= 31) return `Due in ${n} days`;
  return it.due ? 'Due ' + fmtDate(it.due) : '';
}

function ItemForm({ item, limit, people, devices, onClose, onSaved }) {
  const editing = !!item;
  const [f, setF] = useState(() => ({
    name: item ? item.name : '',
    every: item ? (item.every ? String(item.every) : '') : '30',
    unit: item ? item.unit || 'days' : 'days',
    runtimeHours: limit ? String(limit) : '',
    runtimeEntity: (item && item.runtimeEntity) || '',
    owner: (item && item.owner) || '',
    lead: item ? String(item.lead ?? 7) : '7',
    eveningBefore: !!(item && item.eveningBefore),
    askWho: item ? item.askWho !== false : true,
    lastDone: (item && item.lastDoneDate) || todayIso(),
  }));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const set = (p) => setF((s) => ({ ...s, ...p }));
  const save = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    const body = {
      name: f.name.trim(),
      every: f.every === '' ? 0 : Number(f.every),
      unit: f.unit,
      runtimeHours: f.runtimeHours === '' ? null : Number(f.runtimeHours),
      runtimeEntity: f.runtimeHours === '' ? '' : f.runtimeEntity,
      owner: f.owner,
      lead: f.lead === '' ? 7 : Number(f.lead),
      eveningBefore: f.eveningBefore,
      askWho: f.owner ? false : f.askWho,
      lastDone: f.lastDone || todayIso(),
    };
    try {
      if (editing) await api.put('api/upkeep/' + enc(item.id), body, { quiet: true });
      else await api.post('api/upkeep', body, { quiet: true });
      toast(editing ? `Saved ${body.name}` : `Added ${body.name}`, 'success');
      onSaved();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={editing ? `Edit ${item.name}` : 'Add an upkeep item'} onClose={onClose}>
      <form className="uk-form" onSubmit={save} noValidate>
        <label className="field">
          <span className="field-label">Name</span>
          <input className="input" value={f.name} onChange={(e) => set({ name: e.target.value })} placeholder="Change the furnace filter" maxLength={60} autoFocus aria-label="Name" />
        </label>
        <div className="field">
          <span className="field-label">How often</span>
          <span className="uk-line">
            every
            <input className="input input--sm uk-num" type="number" min={1} value={f.every} onChange={(e) => set({ every: e.target.value })} aria-label="Every how many" />
            <select className="select select--sm" value={f.unit} onChange={(e) => set({ unit: e.target.value })} aria-label="Unit">
              {UNITS.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
          </span>
          <span className="uk-line">
            {f.every ? 'or' : ''} after
            <input className="input input--sm uk-num" type="number" min={1} value={f.runtimeHours} onChange={(e) => set({ runtimeHours: e.target.value })} placeholder="–" aria-label="Running hours" />
            running hours of
            <select className="select select--sm" value={f.runtimeEntity} onChange={(e) => set({ runtimeEntity: e.target.value })} aria-label="Which device's running hours" disabled={!devices.length}>
              <option value="">{devices.length ? 'Choose a device…' : 'No device to count'}</option>
              {devices.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.name}
                </option>
              ))}
            </select>
          </span>
          <span className="help">Running hours count the time it's heating or cooling (or on). Leave either empty to use just the other.</span>
        </div>
        <div className="uk-two">
          <label className="field">
            <span className="field-label">Belongs to</span>
            <select className="select" value={f.owner} onChange={(e) => set({ owner: e.target.value })} aria-label="Belongs to">
              <option value="">Everyone</option>
              {people.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Last done</span>
            <input className="input" type="date" value={f.lastDone} max={todayIso()} onChange={(e) => set({ lastDone: e.target.value })} aria-label="Last done" />
          </label>
        </div>
        <span className="uk-line">
          Show as coming up
          <input className="input input--sm uk-num" type="number" min={0} value={f.lead} onChange={(e) => set({ lead: e.target.value })} aria-label="Days before" />
          days before
        </span>
        <Switch checked={f.eveningBefore} onChange={(v) => set({ eveningBefore: v })} label="Heads-up the evening before" />
        {!f.owner ? <Switch checked={f.askWho} onChange={(v) => set({ askWho: v })} label="Ask who did it on the wall tablet" /> : null}
        {error ? (
          <div className="auth-error" role="alert">
            {error}
          </div>
        ) : null}
        <div className="inline-form">
          <button type="submit" className="btn btn-primary" disabled={busy}>
            {busy ? 'Saving…' : editing ? 'Save' : 'Add'}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

function TodaySettings({ isAdmin }) {
  const res = useApi('api/today/settings');
  const [thr, setThr] = useState(null);
  const [from, setFrom] = useState('');
  useEffect(() => {
    if (res.data) {
      setThr(Number(res.data.todayRainThreshold ?? 50));
      setFrom(res.data.todayEveningFrom || '17:00');
    }
  }, [res.data]);
  const save = async (patch) => {
    try {
      await api.patch('api/today/settings', patch);
      toast('Today settings saved', 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };
  return (
    <section className="card prof-card" aria-labelledby="uk-today">
      <h2 id="uk-today" className="prof-title">
        Today on the wall
      </h2>
      {thr === null ? (
        res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
      ) : (
        <>
          <label className="field">
            <span className="field-label">
              Rain heads-up when the chance is at least <strong>{thr ? thr + '%' : 'Off'}</strong>
            </span>
            <input
              type="range"
              min={0}
              max={100}
              step={5}
              value={thr}
              disabled={!isAdmin}
              aria-label="Rain heads-up threshold"
              aria-valuetext={thr ? thr + '%' : 'Off'}
              onChange={(e) => setThr(Number(e.target.value))}
              onPointerUp={() => save({ todayRainThreshold: thr })}
              onKeyUp={() => save({ todayRainThreshold: thr })}
            />
            <span className="help">0 turns it off.</span>
          </label>
          <label className="field">
            <span className="field-label">Evening heads-ups from</span>
            <input className="input" type="time" value={from} disabled={!isAdmin} onChange={(e) => (setFrom(e.target.value), e.target.value && save({ todayEveningFrom: e.target.value }))} aria-label="Evening heads-ups from" />
            <span className="help">From then on, Today mentions tomorrow's garbage day and items due tomorrow.</span>
          </label>
        </>
      )}
    </section>
  );
}

export default function Upkeep() {
  const { isAdmin } = useAuth();
  const confirm = useConfirm();
  const res = useApi('api/upkeep');
  const [form, setForm] = useState(null); // { item? }
  const [justDone, setJustDone] = useState({});
  const items = (res.data && res.data.items) || [];
  const people = (res.data && res.data.people) || [];
  const devices = (res.data && res.data.runtimeDevices) || [];
  const devName = (id) => (devices.find((d) => d.id === id) || {}).name || id;
  // The running-hours limit: the item's device (kind "upkeep") reports it as attrs.runtimeLimit;
  // the item's own runtimeHours is the count so far.
  const { entitiesById } = useHub();
  const limitOf = (it) => (it && it.runtimeLimit) || (it && entitiesById[it.entity] && entitiesById[it.entity].attrs && entitiesById[it.entity].attrs.runtimeLimit) || null;

  const done = async (it) => {
    try {
      await api.post('api/upkeep/' + enc(it.id) + '/done', {});
      setJustDone((s) => ({ ...s, [it.id]: true }));
      toast(`${it.name}: done`, 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };
  const undo = async (it) => {
    try {
      await api.post('api/upkeep/' + enc(it.id) + '/undo', {});
      setJustDone((s) => ({ ...s, [it.id]: false }));
      res.reload();
    } catch {
      /* toast shown */
    }
  };
  const remove = async (it) => {
    if (!(await confirm({ title: `Delete “${it.name}”?`, body: 'Its history goes too. Automations that use it stop working.', confirmLabel: 'Delete', danger: true }))) return;
    try {
      await api.del('api/upkeep/' + enc(it.id));
      toast(`Deleted ${it.name}`, 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };

  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Upkeep</h1>
        <span className="muted head-hint">Things the house needs now and then. They show on the wall's Today and To do.</span>
        {isAdmin ? (
          <button type="button" className="btn btn-primary" style={{ marginLeft: 'auto' }} onClick={() => setForm({})}>
            <Icon name="plus" size={16} /> Add an item
          </button>
        ) : null}
      </div>
      <div className="uk-grid">
        <section className="pane" aria-label="Upkeep items">
          <div className="pane-body">
            {!res.data ? (
              res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
            ) : !items.length ? (
              <Empty title="Nothing yet">{isAdmin ? 'Add the first thing to keep up with — the furnace filter, water softener salt, the car.' : 'An admin can add things to keep up with.'}</Empty>
            ) : (
              <div className="rows">
                {items.map((it) => {
                  const [stLabel, stClass] = STATE[it.state] || STATE.ok;
                  return (
                    <div key={it.id} className="row uk-row">
                      <span className="uk-icon" aria-hidden="true">
                        <Icon name={it.icon || 'wrench'} size={18} />
                      </span>
                      <div className="grow uk-main">
                        <div className="strong">
                          {it.name}
                          <span className={'prof-state ' + stClass}>{stLabel}</span>
                          {it.snoozed ? <span className="prof-state">Tomorrow</span> : null}
                        </div>
                        <div className="phone-meta">
                          {[dueText(it), it.ownerName || 'everyone', it.lastDoneDate ? `last done ${fmtDate(it.lastDoneDate)}${it.lastDoneBy ? ' · ' + it.lastDoneBy : ''}` : '', it.runtimeEntity && typeof it.runtimeHours === 'number' ? `${Math.round(it.runtimeHours)}${limitOf(it) ? ' of ' + limitOf(it) : ''} h of ${devName(it.runtimeEntity)}` : '']
                            .filter(Boolean)
                            .join(' · ')}
                        </div>
                      </div>
                      {justDone[it.id] ? (
                        <button type="button" className="btn btn-sm2" onClick={() => undo(it)}>
                          Undo
                        </button>
                      ) : (
                        <button type="button" className="btn btn-sm2" onClick={() => done(it)}>
                          <Icon name="check" size={14} /> Done
                        </button>
                      )}
                      {isAdmin ? (
                        <MoreMenu
                          label={`More for ${it.name}`}
                          items={[
                            { label: 'Edit…', onClick: () => setForm({ item: it }) },
                            { label: 'Delete…', danger: true, onClick: () => remove(it) },
                          ]}
                        />
                      ) : null}
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </section>
        <div className="uk-side">
          <TodaySettings isAdmin={isAdmin} />
        </div>
      </div>
      {form ? <ItemForm item={form.item || null} limit={limitOf(form.item)} people={people} devices={devices} onClose={() => setForm(null)} onSaved={res.reload} /> : null}
    </div>
  );
}
