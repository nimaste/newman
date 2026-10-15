// Automation builder: "fill-in-the-blank" rows in three sections that read as
// a sentence — When · What happens · Only if. Devices are picked in a popover.
// The header, the live sentence and the right column never move; only the
// builder pane scrolls.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, ApiError, enc, toast } from '../api.js';
import { navigate, useApi, useHub } from '../hooks.js';
import { useLiveRefresh } from '../live.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Check, Loading, ErrorBox, MoreMenu, Switch } from '../components/Controls.jsx';
import { DeviceBlanks } from '../components/DevicePicker.jsx';
import { Icon } from '../icons.jsx';
import { DAY_LETTERS, DAY_NAMES, fmtClock, fmtHHMM, fmtWhen, initial, platformLabel, plural } from '../format.js';
import { actionCommands, activeLabel, automationBody, choiceItems, eventsFor, stateChoices, valueText } from '../model.js';
import { ChoiceChips } from '../components/ChoiceChips.jsx';
import {
  ALL_DAYS,
  MONTHS,
  MONTH_DAYS,
  mmdd,
  parseMMDD,
  actionLabel,
  canHold,
  cleanForSend,
  cleanUntil,
  activeWord,
  availableCommands,
  blockSummaries,
  buttonChoices,
  defaultActionValue,
  explicitState,
  groupIdsByKind,
  kindOfIds,
  newAutomation,
  normalize,
  patchTargets,
  setTargets,
  triggerItems,
  triggerKind,
  momentCount,
  newTriggerItem,
  offsetText,
  DEFAULT_WITHIN,
  WITHIN_UNITS,
  VALUE_OPS,
  findProp,
  formatPropValue,
  isValueRow,
  opLabel,
  propertyStart,
  propsFor,
  sortButtons,
  toMulti,
} from '../automation.js';

const WINDOWS = [
  ['dark', 'After dark', '19:00', '06:00'],
  ['day', 'Daytime', '07:00', '19:00'],
  ['night', 'Overnight', '22:00', '06:00'],
];
const UNITS = ['seconds', 'minutes', 'hours', 'days'];
const HOLD_UNITS = ['seconds', 'minutes', 'hours'];

/**
 * "…for how long" on a When part (a state other than "any", a value compared, not "changes"):
 * it fires once that has held for the time given. Empty amount = no hold.
 */
function HoldBlank({ item, setItem, label }) {
  const hold = item.for;
  if (!canHold(item)) return null;
  if (!hold) {
    return (
      <button type="button" className="add-dev" onClick={() => setItem({ for: { amount: '', unit: 'minutes' } })}>
        <Icon name="clock" size={14} /> for how long
      </button>
    );
  }
  return (
    <span className="blank-seq">
      for
      <input
        type="number"
        min={1}
        className="blank blank--input blank--num"
        aria-label={`For how long (${label})`}
        placeholder="10"
        autoFocus={hold.amount === ''}
        value={hold.amount ?? ''}
        onChange={(e) => setItem({ for: { ...hold, amount: e.target.value === '' ? '' : Number(e.target.value) } })}
        onBlur={(e) => e.target.value === '' && setItem({ for: undefined })}
      />
      <select className="blank blank--select" aria-label={`Unit (${label})`} value={hold.unit || 'minutes'} onChange={(e) => setItem({ for: { ...hold, unit: e.target.value } })}>
        {HOLD_UNITS.map((u) => (
          <option key={u} value={u}>
            {u}
          </option>
        ))}
      </select>
      <button type="button" className="blank-x" aria-label={`Any length of time (remove “for”, ${label})`} onClick={() => setItem({ for: undefined })}>
        <Icon name="x" size={12} strokeWidth={2.6} />
      </button>
    </span>
  );
}
const REMIND = [
  ['', 'never'],
  ['15', '15 minutes'],
  ['30', '30 minutes'],
  ['60', '1 hour'],
  ['120', '2 hours'],
];
const REMIND_TIMES = [1, 2, 3, 4, 5]; // the hub allows 1-5; older automations (none set) remind 3 times

function daysPreset(days) {
  const d = [...(days || [])].sort().join();
  if (d === '0,1,2,3,4,5,6') return 'every';
  if (d === '0,1,2,3,4') return 'weekdays';
  if (d === '5,6') return 'weekends';
  return 'custom';
}

function DayToggles({ days, onChange, label }) {
  const toggle = (i) => onChange(days.includes(i) ? days.filter((d) => d !== i) : [...days, i].sort());
  return (
    <span className="days days--inline" role="group" aria-label={label}>
      {DAY_LETTERS.map((l, i) => (
        <button key={i} type="button" className="day" aria-pressed={days.includes(i)} aria-label={DAY_NAMES[i]} onClick={() => toggle(i)}>
          {l}
        </button>
      ))}
    </span>
  );
}

function Row({ tag, children, onRemove, removeLabel = 'Remove', sub }) {
  return (
    <div className="brow">
      <div className="brow-main">
        {tag ? <span className="brow-tag">{tag}</span> : null}
        <div className="brow-line">{children}</div>
        {onRemove ? (
          <button type="button" className="brow-x" aria-label={removeLabel} title={removeLabel} onClick={onRemove}>
            <Icon name="x" size={16} />
          </button>
        ) : null}
      </div>
      {sub ? <div className="brow-sub">{sub}</div> : null}
    </div>
  );
}

/**
 * Put picked devices into row `i` of a list of rows (trigger items or Only-if
 * rows). A row holds one kind of device: devices of other kinds start new rows
 * right after it. `blank(kind)` gives the other fields for a fresh row.
 */
function placeIds(list, i, ids, hub, blank) {
  const cur = list[i];
  const groups = groupIdsByKind(ids, hub);
  if (!groups.length) return list.map((x, j) => (j === i ? { ...x, entities: [] } : x));
  const curKind = kindOfIds(cur.entities, hub);
  const head = groups.find((g) => g.kind === curKind) || groups[0];
  const rest = groups.filter((g) => g !== head);
  const first = head.kind === curKind ? { ...cur, entities: head.ids } : { ...blank(head.kind), entities: head.ids };
  return [...list.slice(0, i), first, ...rest.map((g) => ({ ...blank(g.kind), entities: g.ids })), ...list.slice(i + 1)];
}

/* ---------------- Values a device reports ---------------- */

const hasProps = (e) => !!(e && (e.properties || []).length);
/** A device that reports value propId (any device with values while none is chosen yet). */
const reportsProp = (e, propId) => hasProps(e) && (!propId || e.properties.some((p) => p.id === propId));

/** The compare-with blank, by the value's type: number + unit, one of the known options, yes/no, or free text. */
function PropValueInput({ p, value, onChange, label }) {
  if (p.type === 'number') {
    return (
      <>
        <input
          type="number"
          step="any"
          className="blank blank--input blank--num"
          aria-label={`Compare with (${label})`}
          value={value ?? ''}
          onChange={(e) => onChange(e.target.value === '' ? null : Number(e.target.value))}
        />
        {p.unit ? <span>{p.unit}</span> : null}
      </>
    );
  }
  if (p.type === 'boolean') {
    const on = value === true || /^(true|1|yes|on)$/i.test(String(value));
    return (
      <select className="blank blank--select" aria-label={`Compare with (${label})`} value={on ? 'true' : 'false'} onChange={(e) => onChange(e.target.value === 'true')}>
        <option value="true">yes</option>
        <option value="false">no</option>
      </select>
    );
  }
  if ((p.options || []).length) {
    return (
      <select className="blank blank--select" aria-label={`Compare with (${label})`} value={value ?? ''} onChange={(e) => onChange(e.target.value)}>
        {value === null || value === undefined || value === '' ? <option value="">Choose…</option> : null}
        {p.options.map((o) => (
          <option key={String(o.value)} value={o.value}>
            {o.label}
          </option>
        ))}
      </select>
    );
  }
  return <input className="blank blank--input" aria-label={`Compare with (${label})`} value={value ?? ''} placeholder={p.value !== null && p.value !== undefined ? String(p.value) : ''} onChange={(e) => onChange(e.target.value)} />;
}

/**
 * [humidity · 54% ▾] [goes above ▾] [60] %: which of the devices' values (entity.properties
 * from the hub), how to compare it (by its type) and with what.
 */
function PropertyControls({ ids, spec, onChange, hub, mode, inWhen, label }) {
  if (!ids.length) return null;
  const props = propsFor(ids, hub);
  const p = props.find((x) => x.id === spec.property) || null;
  if (!props.length) return <span className="muted small">reports no values</span>;
  const ops = p ? (VALUE_OPS[p.type] || VALUE_OPS.text).filter((o) => inWhen || o !== 'changes') : [];
  return (
    <>
      <select
        className={'blank blank--select' + (p ? '' : ' blank--empty')}
        aria-label={`Which value (${label})`}
        value={p ? p.id : ''}
        onChange={(e) => {
          const np = props.find((x) => x.id === e.target.value);
          if (np) onChange({ property: np.id, ...propertyStart(np, inWhen) });
        }}
      >
        {!p ? (
          <option value="" disabled>
            Choose a value…
          </option>
        ) : null}
        {props.map((x) => (
          <option key={x.id} value={x.id}>
            {x.label.charAt(0).toLowerCase() + x.label.slice(1)} · now {formatPropValue(x, x.value)}
          </option>
        ))}
      </select>
      {p ? (
        <select className={'blank blank--select' + (spec.op ? '' : ' blank--empty')} aria-label={`How to compare (${label})`} value={ops.includes(spec.op) ? spec.op : ''} onChange={(e) => onChange({ op: e.target.value })}>
          {!ops.includes(spec.op) ? (
            <option value="" disabled>
              compare…
            </option>
          ) : null}
          {ops.map((o) => (
            <option key={o} value={o}>
              {opLabel(o, mode)}
            </option>
          ))}
        </select>
      ) : null}
      {p && spec.op && spec.op !== 'changes' ? <PropValueInput p={p} value={spec.value} onChange={(v) => onChange({ value: v })} label={label} /> : null}
    </>
  );
}

/* ---------------- When ---------------- */

const KIND_CHOICES = [
  { id: 'time', icon: 'clock', title: 'At a time', hint: 'Every day at 9 PM, at sunset…' },
  { id: 'state', icon: 'door', title: 'A device is / becomes…', hint: 'A lock unlocks, a light is on…' },
  { id: 'event', icon: 'bell', title: 'Something happens', hint: 'A device reports something: a button press, a detection…' },
  { id: 'property', icon: 'thermometer', title: 'A value / reading', hint: 'Temperature, humidity, battery… goes above or below' },
];

const SUN_OFFSETS = [-120, -90, -60, -45, -30, -15, 0, 15, 30, 45, 60, 90, 120];

/** "exactly" / "30 min before" / "1 hour after" - followed by the word sunrise or sunset. */
function OffsetSelect({ value, onChange, label }) {
  const v = Math.round(Number(value) || 0);
  const opts = SUN_OFFSETS.includes(v) ? SUN_OFFSETS : [...SUN_OFFSETS, v].sort((a, b) => a - b);
  return (
    <select className="blank blank--select" aria-label={label} value={v} onChange={(e) => onChange(Number(e.target.value))}>
      {opts.map((o) => (
        <option key={o} value={o}>
          {o ? `${offsetText(o)} ${o < 0 ? 'before' : 'after'}` : 'exactly'}
        </option>
      ))}
    </select>
  );
}

function DaysSelect({ days, onChange }) {
  const preset = daysPreset(days);
  return (
    <>
      <select
        className="blank blank--select"
        aria-label="Which days"
        value={preset}
        onChange={(e) => {
          const v = e.target.value;
          if (v === 'every') onChange(ALL_DAYS.slice());
          else if (v === 'weekdays') onChange([0, 1, 2, 3, 4]);
          else if (v === 'weekends') onChange([5, 6]);
          else if (preset !== 'custom') onChange([0, 2, 4]);
        }}
      >
        <option value="every">every day</option>
        <option value="weekdays">weekdays</option>
        <option value="weekends">weekends</option>
        <option value="custom">these days…</option>
      </select>
      {preset === 'custom' ? <DayToggles days={days || []} onChange={onChange} label="Days" /> : null}
    </>
  );
}

/** One "When" part: a time, a device state, or something a device reports. */
function TriggerRow({ item, i, count, allOf, hub, setItems }) {
  const setItem = (p) => setItems((l) => l.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const joiner = allOf ? 'and' : 'or';
  const lead = (first, later) => <span className={i ? 'joiner joiner--row' : ''}>{i ? later : first}</span>;
  const remove = () => setItems((l) => l.filter((_, j) => j !== i));
  const common = { onRemove: remove, removeLabel: `Remove “When” part ${i + 1}` };

  if (item.type === 'time') {
    // A clock time, or sunrise / sunset with an offset ("At 30 min before sunset").
    const what = (
      <select
        className="blank blank--select"
        aria-label={`Clock time or the sun (part ${i + 1})`}
        value={item.sun || 'clock'}
        onChange={(e) => {
          const v = e.target.value;
          if (v === 'clock') {
            // eslint-disable-next-line no-unused-vars
            const { sun, offset, ...rest } = item;
            setItems((l) => l.map((x, j) => (j === i ? rest : x)));
          } else setItem({ sun: v, offset: item.offset || 0 });
        }}
      >
        <option value="clock">a set time</option>
        {['sunrise', 'sunset', ...(item.sun && !['sunrise', 'sunset'].includes(item.sun) ? [item.sun] : [])].map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))}
      </select>
    );
    return (
      <Row {...common}>
        {lead('At', `${joiner} at`)}
        {item.sun ? (
          <>
            <OffsetSelect value={item.offset || 0} onChange={(offset) => setItem({ offset })} label={`How long before or after ${item.sun} (part ${i + 1})`} />
            {what}
          </>
        ) : (
          <>
            {what}
            <input type="time" className="blank blank--input" aria-label={`Time (part ${i + 1})`} value={item.time || ''} onChange={(e) => e.target.value && setItem({ time: e.target.value })} />
          </>
        )}
        on
        <DaysSelect days={item.days} onChange={(days) => setItem({ days })} />
      </Row>
    );
  }

  if (item.type === 'event') {
    const e = hub.entitiesById[item.entity];
    const k = e && hub.kindsById[e.kind];
    // Events come from /api/meta (the kind's events, narrowed to the device's own attrs.events), in the kind's order.
    const evs = e ? eventsFor(e, k) : [];
    return (
      <Row {...common}>
        {lead('When', joiner)}
        <DeviceBlanks
          single
          ids={item.entity ? [item.entity] : []}
          emptyLabel="Choose a device"
          include={(d, dk) => eventsFor(d, dk).length > 0}
          onChange={(ids) => {
            const ne = hub.entitiesById[ids[0]];
            const nevs = ne ? eventsFor(ne, hub.kindsById[ne.kind]) : [];
            const keep = nevs.some((x) => x.id === item.event);
            // Only one thing it can do: that's the choice. Otherwise the person picks (no default).
            setItem({ entity: ids[0] || '', event: keep ? item.event : nevs.length === 1 ? nevs[0].id : '' });
          }}
        />
        {e && evs.length ? (
          evs.length === 1 ? (
            <span>{evs[0].label}</span>
          ) : (
            <select className={'blank blank--select' + (item.event ? '' : ' blank--empty')} aria-label={`What happens (part ${i + 1})`} value={item.event || ''} onChange={(ev) => setItem({ event: ev.target.value })}>
              {!item.event ? (
                <option value="" disabled>
                  Choose what happens…
                </option>
              ) : null}
              {evs.map((ev) => (
                <option key={ev.id} value={ev.id}>
                  {ev.label}
                </option>
              ))}
            </select>
          )
        ) : null}
      </Row>
    );
  }

  if (item.type === 'property') {
    // Any device that reports the chosen value can join, whatever its kind ("the battery at A, B or C").
    return (
      <Row {...common}>
        {lead('When', joiner)}
        <DeviceBlanks
          ids={item.entities}
          joiner={allOf ? 'and' : 'or'}
          include={(e) => reportsProp(e, item.property)}
          swapInclude={item.entities.length === 1 ? hasProps : (e) => reportsProp(e, item.property)}
          emptyLabel="Choose a device"
          onChange={(ids) => setItem({ entities: ids })}
        />
        <PropertyControls ids={item.entities} spec={item} onChange={setItem} hub={hub} mode={allOf && count > 1 ? 'is' : 'goes'} inWhen label={`part ${i + 1}`} />
        {item.entities.length ? <HoldBlank item={item} setItem={setItem} label={`part ${i + 1}`} /> : null}
      </Row>
    );
  }

  // A device is / becomes …: one kind of device per row; other kinds start their own row.
  const stateful = (e, k) => !!(k && (k.states || []).length > 1);
  const kind = kindOfIds(item.entities, hub);
  const k = kind && hub.kindsById[kind];
  const sameKind = (e, ek) => stateful(e, ek) && (!kind || e.kind === kind);
  const plural = item.entities.length > 1;
  const badAny = allOf && count > 1 && item.to === 'any' && item.entities.length > 0;
  const blank = (am) => (kd) => {
    const bk = hub.kindsById[kd];
    return { type: 'state', to: am ? (bk && (bk.activeState || ((bk.states || [])[0] || {}).id)) || 'any' : 'any' };
  };
  return (
    <Row {...common} sub={badAny ? <span className="amber">When all must be true together, pick the state for each device.</span> : null}>
      {lead('When', joiner)}
      <DeviceBlanks
        ids={item.entities}
        joiner={allOf ? 'and' : 'or'}
        include={sameKind}
        swapInclude={item.entities.length === 1 ? stateful : sameKind}
        emptyLabel="Choose devices"
        onChange={(ids) => setItems((l, am) => placeIds(l, i, ids, hub, blank(am)))}
      />
      {item.entities.length ? (
        <select className="blank blank--select" aria-label={`What has to happen (part ${i + 1})`} value={item.to || 'any'} onChange={(e) => setItem({ to: e.target.value })}>
          <option value="any" disabled={allOf && count > 1 && item.to !== 'any'}>
            changes at all
          </option>
          {((k && k.states) || []).map((s) => (
            <option key={s.id} value={s.id}>
              {allOf && count > 1 ? (plural ? 'are ' : 'is ') : 'becomes '}
              {s.label.toLowerCase()}
            </option>
          ))}
        </select>
      ) : null}
      {item.entities.length ? <HoldBlank item={item} setItem={setItem} label={`part ${i + 1}`} /> : null}
    </Row>
  );
}

function WhenSection({ auto, patch, hub }) {
  const t = auto.trigger;
  const items = triggerItems(t);
  const allOf = t.match === 'all';
  const within = t.within || DEFAULT_WITHIN;
  const setTrigger = (p) => patch((a) => ({ ...a, trigger: { ...a.trigger, ...p } }));
  const setItems = (fn) => patch((a) => patchTargets(a, (x) => ({ ...x, trigger: { ...x.trigger, items: fn(triggerItems(x.trigger), x.trigger.match === 'all') } }), hub));
  const hasEventDevices = (hub.entities || []).some((e) => eventsFor(e, hub.kindsById[e.kind]).length > 0);
  const hasValueDevices = (hub.entities || []).some(hasProps);
  const kinds = KIND_CHOICES.filter((o) => (o.id !== 'event' || hasEventDevices) && (o.id !== 'property' || hasValueDevices));
  const add = (type) => setItems((l) => [...l, newTriggerItem(type)]);
  const showWithin = allOf && items.length > 1 && momentCount(t) >= 2;

  return (
    <section className="bsec" aria-labelledby="sec-when">
      <div className="bsec-head">
        <h2 id="sec-when" className="bsec-title">
          When
        </h2>
        <span className="bsec-hint">{items.length > 1 ? 'Mix times, devices and things that happen.' : items.length ? '' : 'What starts it?'}</span>
      </div>
      {items.length > 1 ? (
        <div className="joinpick-wrap">
          <div className="joinpick" role="radiogroup" aria-label="How these combine">
            {[
              ['any', 'any', 'of these happens'],
              ['all', 'all', 'of these are true together'],
            ].map(([id, word, rest]) => (
              <button key={id} type="button" role="radio" aria-checked={(allOf ? 'all' : 'any') === id} className="joinpick-opt" onClick={() => setTrigger({ match: id })}>
                <span className="joinpick-dot" aria-hidden="true" />
                <span>
                  Start when <b>{word}</b> {rest}
                </span>
              </button>
            ))}
          </div>
          {showWithin ? (
            <div className="within-line">
              <span>…within</span>
              <input
                type="number"
                min={1}
                className="blank blank--input blank--num"
                aria-label="Within how many"
                value={within.amount ?? ''}
                onChange={(e) => setTrigger({ within: { ...within, amount: e.target.value === '' ? '' : Math.max(1, Math.round(Number(e.target.value) || 1)) } })}
              />
              <select className="blank blank--select" aria-label="Within unit" value={within.unit || 'minutes'} onChange={(e) => setTrigger({ within: { ...within, unit: e.target.value } })}>
                {WITHIN_UNITS.map((u) => (
                  <option key={u} value={u}>
                    {u}
                  </option>
                ))}
              </select>
              <span>of each other</span>
              <span className="within-hint">Detections and times count for that long; device states count while they last.</span>
            </div>
          ) : null}
        </div>
      ) : null}
      {!items.length ? (
        <div className="brow brow--choose">
          <div className="ttypes ttypes--choose" role="group" aria-label="What starts it">
            {kinds.map((o) => (
              <button key={o.id} type="button" className="ttype ttype--choose" title={o.hint} onClick={() => add(o.id)}>
                <Icon name={o.icon} size={16} /> {o.title}
              </button>
            ))}
          </div>
        </div>
      ) : (
        items.map((it, i) => <TriggerRow key={i} item={it} i={i} count={items.length} allOf={allOf} hub={hub} setItems={setItems} />)
      )}
      {items.length ? (
        <div className="add-row">
          <AddMenu label="+ Add another…" items={kinds.map((o) => ({ label: o.title, onClick: () => add(o.id) }))} />
        </div>
      ) : null}
    </section>
  );
}

/* ---------------- Only if ---------------- */

const ALWAYS_LABEL = 'Always (just tell me how they are)';

/** One "Only if [devices] [is on ▾]" row. */
function DeviceCheckRow({ row, i, first, isTime, hub, setRows }) {
  const stateful = (e, k) => !!(k && ((k.states || []).length > 1 || k.activeState));
  // A row checks a state ("is locked") or a value the device reports ("humidity is above 60%").
  const checkable = (e, k) => stateful(e, k) || hasProps(e);
  const kind = kindOfIds(row.entities, hub);
  const k = kind && hub.kindsById[kind];
  const sameKind = (e, ek) => checkable(e, ek) && (!kind || e.kind === kind);
  // Value rows mix kinds freely (any device reporting that value); state rows keep one kind per row.
  const valueRow = isValueRow(row);
  const canState = !!k && stateful(null, k);
  const canValue = row.entities.length > 0 && propsFor(row.entities, hub).length > 0;
  const replaceRow = (next) => setRows((l) => l.map((x, j) => (j === i ? next : x)));
  const toValue = () => replaceRow({ entities: row.entities, match: row.match === 'all' || row.match === 'none' ? row.match : 'any', property: '', op: '', value: null });
  const toState = () => replaceRow({ entities: row.entities, match: row.match === 'always' ? 'any' : row.match, state: (k && (k.activeState || ((k.states || [])[0] || {}).id)) || 'active' });
  const states = (k && k.states) || [];
  // Legacy rows say "active": show the kind's own active state, write the explicit one once edited.
  const shown = explicitState(row.entities, row.state, hub);
  const setRow = (p) => setRows((l) => l.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const blank = (kd) => {
    const bk = hub.kindsById[kd];
    // Devices without states (a temperature sensor) start as a value row.
    if (!stateful(null, bk)) return { match: 'any', property: '', op: '', value: null };
    return { state: (bk && (bk.activeState || ((bk.states || [])[0] || {}).id)) || 'active', match: 'any' };
  };
  const n = row.entities.length;
  const stateOpts = states.length ? [...states] : [{ id: 'active', label: activeLabel(k) || 'on' }];
  if (!states.some((s) => s.id === shown) && shown === 'active') stateOpts.unshift({ id: 'active', label: 'on (each its own)' });

  let chooser = null;
  if (valueRow && n) {
    chooser = (
      <>
        {n > 1 ? (
          <select className="blank blank--select" aria-label={`How many (row ${i + 1})`} value={['any', 'all', 'none'].includes(row.match) ? row.match : 'any'} onChange={(e) => setRow({ match: e.target.value })}>
            <option value="any">— on any of them,</option>
            <option value="all">— on all of them,</option>
            <option value="none">— on none of them,</option>
          </select>
        ) : null}
        <PropertyControls ids={row.entities} spec={row} onChange={setRow} hub={hub} mode="is" inWhen={false} label={`row ${i + 1}`} />
        {canState ? (
          <button type="button" className="add-dev" onClick={toState}>
            use its state instead
          </button>
        ) : null}
      </>
    );
  } else if (n === 1) {
    // One device: a single blank, "is on" / "isn't on".
    const value = row.match === 'always' ? 'always' : `${row.match === 'none' ? 'none' : 'any'}|${shown}`;
    chooser = (
      <select
        className="blank blank--select"
        aria-label={`State to check (row ${i + 1})`}
        value={value}
        onChange={(e) => {
          const v = e.target.value;
          if (v === 'always') setRow({ match: 'always' });
          else if (v === '__value') toValue();
          else {
            const [match, state] = v.split('|');
            setRow({ match, state });
          }
        }}
      >
        {stateOpts.map((s) => (
          <option key={'is' + s.id} value={`any|${s.id}`}>
            is {s.label.toLowerCase()}
          </option>
        ))}
        {stateOpts.map((s) => (
          <option key={'not' + s.id} value={`none|${s.id}`}>
            isn’t {s.label.toLowerCase()}
          </option>
        ))}
        {isTime ? <option value="always">— {ALWAYS_LABEL.charAt(0).toLowerCase() + ALWAYS_LABEL.slice(1)}</option> : null}
        {canValue ? <option value="__value">— one of its values (temperature, battery…)</option> : null}
      </select>
    );
  } else if (n > 1) {
    const matchValue = ['any', 'all', 'none'].includes(row.match) || (isTime && row.match === 'always') ? row.match : 'any';
    chooser = (
      <>
        <select className="blank blank--select" aria-label={`How many (row ${i + 1})`} value={matchValue} onChange={(e) => (e.target.value === '__value' ? toValue() : setRow({ match: e.target.value }))}>
          <option value="any">— any is</option>
          <option value="all">— all are</option>
          <option value="none">— none is</option>
          {isTime ? <option value="always">— {ALWAYS_LABEL.charAt(0).toLowerCase() + ALWAYS_LABEL.slice(1)}</option> : null}
          {canValue ? <option value="__value">— one of their values (temperature, battery…)</option> : null}
        </select>
        {row.match !== 'always' ? (
          <select className="blank blank--select" aria-label={`State (row ${i + 1})`} value={shown} onChange={(e) => setRow({ state: e.target.value })}>
            {stateOpts.map((s) => (
              <option key={s.id} value={s.id}>
                {s.label.toLowerCase()}
              </option>
            ))}
          </select>
        ) : null}
      </>
    );
  }

  return (
    <Row
      tag="Device"
      removeLabel={`Remove device check ${i + 1}`}
      onRemove={() => setRows((l) => l.filter((_, j) => j !== i))}
      sub={isTime && row.match === 'always' && n ? 'It runs every time; the notification says how they are and has buttons for them.' : null}
    >
      <span className={first ? '' : 'joiner joiner--row'}>{first ? 'Only if' : 'and'}</span>
      <DeviceBlanks
        ids={row.entities}
        joiner={row.match === 'all' ? 'and' : 'or'}
        include={valueRow ? (e) => reportsProp(e, row.property) : sameKind}
        swapInclude={n === 1 ? checkable : valueRow ? (e) => reportsProp(e, row.property) : sameKind}
        emptyLabel="Choose devices"
        onChange={(ids) => (valueRow ? setRow({ entities: ids }) : setRows((l) => placeIds(l, i, ids, hub, blank)))}
      />
      {chooser}
    </Row>
  );
}

/** [month] [day] - one end of an "only between dates" range ("MM-DD"). */
function MonthDay({ value, onChange, label }) {
  const [m, dy] = parseMMDD(value) || [1, 1];
  const set = (mo, day) => onChange(mmdd(mo, Math.min(day, MONTH_DAYS[mo - 1])));
  return (
    <>
      <select className="blank blank--select" aria-label={`${label} month`} value={m} onChange={(e) => set(Number(e.target.value), dy)}>
        {MONTHS.map((name, i) => (
          <option key={name} value={i + 1}>
            {name}
          </option>
        ))}
      </select>
      <select className="blank blank--select" aria-label={`${label} day`} value={dy} onChange={(e) => set(m, Number(e.target.value))}>
        {Array.from({ length: MONTH_DAYS[m - 1] }, (_, i) => (
          <option key={i} value={i + 1}>
            {i + 1}
          </option>
        ))}
      </select>
    </>
  );
}

function OnlyIfSection({ auto, patch, hub }) {
  const c = auto.condition;
  // A pure time trigger: its rows are also what the notification is about, and it picks its own days.
  const isTime = triggerKind(auto.trigger) === 'time';
  const setCond = (p) => patch((a) => ({ ...a, condition: { ...a.condition, ...p } }));
  const rows = c.devices || [];
  const setRows = (fn) => patch((a) => patchTargets(a, (x) => ({ ...x, condition: { ...x.condition, devices: fn(x.condition.devices || []) } }), hub));
  const showTime = !!c.between;
  const showDays = !isTime && Array.isArray(c.days);
  const showDevice = rows.length > 0;
  const b = c.between || {};
  const preset = (WINDOWS.find(([, , a, z]) => a === b.after && z === b.before) || ['custom'])[0];
  const sun = c.sun && (c.sun.only === 'dark' || c.sun.only === 'light') ? c.sun : null;
  // Dark runs from sunset to sunrise; daylight the other way round.
  const edges = sun && sun.only === 'light' ? ['sunrise', 'sunset'] : ['sunset', 'sunrise'];
  const dates = c.dates && (c.dates.from || c.dates.to) ? c.dates : null;

  return (
    <section className="bsec" aria-labelledby="sec-if">
      <div className="bsec-head">
        <h2 id="sec-if" className="bsec-title">
          Only if
        </h2>
        <span className="bsec-hint">{showTime || showDevice || showDays || sun || dates ? 'It only runs when all of these are true.' : 'Optional — leave empty to run every time.'}</span>
      </div>
      {showTime ? (
        <Row tag="Time" onRemove={() => setCond({ between: null })} removeLabel="Remove time window">
          Only between
          <input type="time" className="blank blank--input" aria-label="From" value={b.after || ''} onChange={(e) => setCond({ between: { ...b, after: e.target.value } })} />
          and
          <input type="time" className="blank blank--input" aria-label="Until" value={b.before || ''} onChange={(e) => setCond({ between: { ...b, before: e.target.value } })} />
          <select
            className="blank blank--select blank--quiet"
            aria-label="Common windows"
            value={preset}
            onChange={(e) => {
              const w = WINDOWS.find(([id]) => id === e.target.value);
              if (w) setCond({ between: { after: w[2], before: w[3] } });
            }}
          >
            {WINDOWS.map(([id, label, a, z]) => (
              <option key={id} value={id}>
                {label} ({fmtHHMM(a).replace(':00', '')}–{fmtHHMM(z).replace(':00', '')})
              </option>
            ))}
            <option value="custom">Custom</option>
          </select>
          {!b.after || !b.before ? <span className="amber small">pick both times</span> : null}
        </Row>
      ) : null}
      {sun ? (
        <Row tag="Sun" onRemove={() => setCond({ sun: null })} removeLabel="Remove sunrise / sunset limit">
          Only
          <select className="blank blank--select" aria-label="Dark or daylight" value={sun.only} onChange={(e) => setCond({ sun: { ...sun, only: e.target.value } })}>
            <option value="dark">when it’s dark</option>
            <option value="light">during daylight</option>
          </select>
          from
          <OffsetSelect value={sun[edges[0]]} onChange={(v) => setCond({ sun: { ...sun, [edges[0]]: v } })} label={`How long before or after ${edges[0]} it starts`} />
          {edges[0]} until
          <OffsetSelect value={sun[edges[1]]} onChange={(v) => setCond({ sun: { ...sun, [edges[1]]: v } })} label={`How long before or after ${edges[1]} it ends`} />
          {edges[1]}
        </Row>
      ) : null}
      {dates ? (
        <Row tag="Dates" onRemove={() => setCond({ dates: null })} removeLabel="Remove date range">
          Only from
          <MonthDay value={dates.from} label="From" onChange={(from) => setCond({ dates: { ...dates, from } })} />
          to
          <MonthDay value={dates.to} label="To" onChange={(to) => setCond({ dates: { ...dates, to } })} />
          {dates.year !== null && dates.year !== undefined ? (
            <input
              type="number"
              className="blank blank--input blank--num"
              aria-label="Year"
              min="2000"
              max="2100"
              value={dates.year}
              onChange={(e) => setCond({ dates: { ...dates, year: e.target.value === '' ? '' : Number(e.target.value) } })}
            />
          ) : null}
          <Check
            checked={dates.year === null || dates.year === undefined}
            onChange={(every) => setCond({ dates: { ...dates, year: every ? null : new Date().getFullYear() } })}
          >
            every year
          </Check>
        </Row>
      ) : null}
      {rows.map((r, i) => (
        <DeviceCheckRow key={i} row={r} i={i} first={i === 0} isTime={isTime} hub={hub} setRows={setRows} />
      ))}
      {showDays ? (
        <Row tag="Days" onRemove={() => setCond({ days: null })} removeLabel="Remove day limit">
          Only on
          <DayToggles days={c.days} onChange={(days) => setCond({ days })} label="Days of the week" />
        </Row>
      ) : null}
      <div className="add-row">
        {!showTime ? (
          <button type="button" className="add-btn" onClick={() => setCond({ between: { after: '19:00', before: '06:00' } })}>
            + Time window
          </button>
        ) : null}
        {!sun ? (
          <button type="button" className="add-btn" onClick={() => setCond({ sun: { only: 'dark', sunset: 0, sunrise: 0 } })}>
            + Dark or daylight
          </button>
        ) : null}
        {!dates ? (
          <button type="button" className="add-btn" onClick={() => setCond({ dates: { from: '12-01', to: '01-06', year: null } })}>
            + Between dates
          </button>
        ) : null}
        <button type="button" className="add-btn" onClick={() => setRows((l) => [...l, { entities: [], state: 'active', match: 'any' }])}>
          + A device is…
        </button>
        {!isTime && !showDays ? (
          <button type="button" className="add-btn" onClick={() => setCond({ days: [0, 1, 2, 3, 4] })}>
            + Day of the week
          </button>
        ) : null}
      </div>
    </section>
  );
}

/* ---------------- What happens ---------------- */

/** Commands that every chosen device supports (automation-only ones included). */
function commonCommands(ids, hub) {
  const ents = ids.map((i) => hub.entitiesById[i]).filter(Boolean);
  if (!ents.length) return [];
  const lists = ents.map((e) => actionCommands(hub.kindsById[e.kind]));
  return lists[0].filter((c) => lists.every((l) => l.some((x) => x.id === c.id)));
}

/**
 * The value blank for a command that takes one, driven purely by the command's
 * valueType (and min/max/step/unit) from /api/meta:
 * percent → slider, number → − / + stepper, state → one of the kind's settable states,
 * choices → chips of the device's items (attrs[cmd.attr]), text → input.
 */
function ValueInput({ cmd, kind, value, onChange, entity }) {
  const vt = cmd.valueType || 'text';
  if (vt === 'choices') return <ChoiceChips items={choiceItems(entity, cmd)} value={value} onChange={onChange} label={cmd.label} />;
  if (vt === 'percent') {
    const v = typeof value === 'number' ? value : cmd.default ?? cmd.min ?? 0;
    return (
      <span className="blank-slider">
        <input type="range" aria-label={cmd.label} min={cmd.min ?? 0} max={cmd.max ?? 100} step={cmd.step || 1} value={v} onChange={(e) => onChange(Number(e.target.value))} />
        <span className="blank-slider-value">{valueText(cmd, kind, v)}</span>
      </span>
    );
  }
  if (vt === 'number') {
    const stepBy = cmd.step || 1;
    const decimals = String(stepBy).includes('.') ? String(stepBy).split('.')[1].length : 0;
    const cur = typeof value === 'number' ? value : cmd.default ?? cmd.min ?? 0;
    const clamp = (n) => Number(Math.min(cmd.max ?? Infinity, Math.max(cmd.min ?? -Infinity, n)).toFixed(decimals));
    return (
      <span className="stepper stepper--blank" role="group" aria-label={cmd.label}>
        <button type="button" className="stepper-btn" aria-label={`Lower: ${cmd.label}`} onClick={() => onChange(clamp(cur - stepBy))}>
          −
        </button>
        <span className="stepper-value" aria-live="polite">
          {Number(cur).toFixed(decimals)}
          <small>{cmd.unit || ''}</small>
        </span>
        <button type="button" className="stepper-btn" aria-label={`Raise: ${cmd.label}`} onClick={() => onChange(clamp(cur + stepBy))}>
          +
        </button>
      </span>
    );
  }
  if (vt === 'state') {
    const states = stateChoices(kind);
    return (
      <select className="blank blank--select" aria-label={cmd.label} value={value || ''} onChange={(e) => onChange(e.target.value)}>
        {!value ? <option value="">Choose…</option> : null}
        {states.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
    );
  }
  return <input className="blank blank--input" aria-label={cmd.label} value={value ?? ''} onChange={(e) => onChange(e.target.value)} />;
}

function ActionRow({ action, onChange, onRemove, hub, idx }) {
  const a = toMulti(action);
  const cmds = commonCommands(a.entities, hub);
  const cmd = cmds.find((c) => c.id === a.command);
  const first = a.entities.map((i) => hub.entitiesById[i]).find(Boolean);
  const kind = first && hub.kindsById[first.kind];
  const setDevices = (ids) => {
    const next = commonCommands(ids, hub);
    const keep = next.find((c) => c.id === a.command);
    const pick = keep || next.find((c) => !c.sensitive) || next[0];
    const e = ids.map((i) => hub.entitiesById[i]).find(Boolean);
    const value = pick && pick.takesValue ? (keep && a.value !== undefined ? a.value : defaultActionValue(pick, e, e && hub.kindsById[e.kind])) : undefined;
    onChange({ entities: ids, command: pick ? pick.id : '', ...(value !== undefined ? { value } : {}), ...(a.for && pick && !pick.automationOnly ? { for: a.for } : {}) });
  };
  const setCommand = (id) => {
    const c = cmds.find((x) => x.id === id);
    onChange({ entities: a.entities, command: id, ...(c && c.takesValue ? { value: defaultActionValue(c, first, kind) } : {}), ...(a.for && !(c && c.automationOnly) ? { for: a.for } : {}) });
  };
  const include = (e, k) => !!(k && (k.commands || []).length);
  const value = a.value;

  return (
    <Row
      tag="Do"
      onRemove={onRemove}
      removeLabel={`Remove action ${idx + 1}`}
      sub={
        <>
          {cmd && cmd.sensitive ? <span className="amber">“{cmd.label}” is sensitive — Newman does it without asking anyone. </span> : null}
          {a.for ? 'Afterwards Newman puts it back the way it was — unless someone changed it in the meantime.' : null}
          {a.entities.length > 1 && !cmds.length ? <span className="amber">These devices have nothing in common to do — remove one.</span> : null}
        </>
      }
    >
      {a.entities.length && cmds.length ? (
        <select className="blank blank--select blank--strong" aria-label="What to do" value={a.command} onChange={(e) => setCommand(e.target.value)}>
          {cmds.map((c) => (
            <option key={c.id} value={c.id}>
              {c.label}
            </option>
          ))}
        </select>
      ) : (
        <span>Control</span>
      )}
      <DeviceBlanks ids={a.entities} joiner="and" include={include} onChange={setDevices} />
      {cmd && cmd.takesValue ? (
        <>
          {cmd.valueType === 'choices' ? ':' : 'to'}
          <ValueInput cmd={cmd} kind={kind} entity={first} value={value} onChange={(v) => onChange({ ...a, value: v })} />
        </>
      ) : null}
      {cmd && !cmd.automationOnly ? (
        a.for ? (
          <span className="blank-seq">
            for
            <input
              type="number"
              min={1}
              className="blank blank--input blank--num"
              aria-label="How many"
              value={a.for.amount}
              onChange={(e) => onChange({ ...a, for: { ...a.for, amount: Math.max(1, Math.round(Number(e.target.value) || 1)) } })}
            />
            <select className="blank blank--select" aria-label="Unit" value={a.for.unit} onChange={(e) => onChange({ ...a, for: { ...a.for, unit: e.target.value } })}>
              {UNITS.map((u) => (
                <option key={u} value={u}>
                  {u}
                </option>
              ))}
            </select>
            <button type="button" className="blank-x" aria-label="Keep it that way (remove the time limit)" onClick={() => onChange({ ...a, for: null })}>
              <Icon name="x" size={12} strokeWidth={2.6} />
            </button>
          </span>
        ) : (
          <button type="button" className="add-dev" onClick={() => onChange({ ...a, for: { amount: 10, unit: 'minutes' } })}>
            <Icon name="clock" size={14} /> for a while
          </button>
        )
      ) : null}
    </Row>
  );
}

function NotifyRow({ auto, patch, hub, people, choices }) {
  const n = auto.notify;
  const f = auto.followUp;
  const setNotify = (p) => patch((a) => ({ ...a, notify: { ...a.notify, ...p } }));
  const setFollow = (p) => patch((a) => ({ ...a, followUp: { ...a.followUp, ...p } }));
  const toggleButton = (id) => setNotify({ buttons: sortButtons(n.buttons.includes(id) ? n.buttons.filter((b) => b !== id) : [...n.buttons, id], choices) });
  const togglePerson = (id) => patch((a) => ({ ...a, recipients: a.recipients.includes(id) ? a.recipients.filter((x) => x !== id) : [...a.recipients, id] }));
  const list = people.data || [];
  const word = activeWord(auto, hub);
  // The snapshot comes from the first event part whose device's kind has a camera (as the hub does).
  const trig = triggerItems(auto.trigger)
    .filter((i) => i.type === 'event')
    .map((i) => hub.entitiesById[i.entity])
    .find((e) => e && hub.kindsById[e.kind] && hub.kindsById[e.kind].hasCamera);
  const snapshot = !!trig;
  const deviceButtons = choices.filter((b) => b.command && n.buttons.includes(b.id));
  // "Tell everyone who did it" needs a command that confirms back (its confirmsTo state shows it worked).
  const followRelevant = deviceButtons.some((b) => !!b.confirmsTo);

  return (
    <Row
      tag="Notify"
      removeLabel="Don't send a notification"
      onRemove={() => patch((a) => ({ ...a, notify: { ...a.notify, enabled: false } }))}
      sub={
        <div className="notify-sub">
          <div className="nsub">
            <span className="nsub-label">Buttons</span>
            <div className="nsub-checks">
              {choices.map((b) => (
                <Check key={b.id} checked={n.buttons.includes(b.id)} onChange={() => toggleButton(b.id)}>
                  <span className="check-line">
                    <Icon name={b.icon} size={14} className={b.sensitive ? 'amber' : 'muted'} />
                    {b.label}
                  </span>
                </Check>
              ))}
            </div>
          </div>
          <div className="nsub">
            <span className="nsub-label">Options</span>
            <div className="nsub-checks">
              {snapshot ? (
                <Check checked={n.snapshot !== false} onChange={(v) => setNotify({ snapshot: v })}>
                  Attach a snapshot from {trig.name}
                </Check>
              ) : null}
              <Check checked={n.timeSensitive} onChange={(v) => setNotify({ timeSensitive: v })}>
                Time-sensitive (breaks through Focus)
              </Check>
              {deviceButtons.length ? (
                <Check checked={n.onlyPending} onChange={(v) => setNotify({ onlyPending: v })}>
                  Only buttons for devices still {word === 'on' ? 'pending' : word}
                </Check>
              ) : null}
              <label className="inline-form nsub-title">
                <span className="muted">Title</span>
                <input className="input input--sm" value={n.title || ''} placeholder={auto.name} onChange={(e) => setNotify({ title: e.target.value })} />
              </label>
            </div>
          </div>
          {followRelevant || f.enabled || f.remindMinutes ? (
            <div className="nsub">
              <span className="nsub-label">Afterwards</span>
              <div className="nsub-checks">
                <span className="inline-form">
                  <Check checked={f.enabled} onChange={(v) => setFollow({ enabled: v })}>
                    When it’s done, tell everyone:
                  </Check>
                  <input className="input input--sm" style={{ maxWidth: 260 }} value={f.message || ''} disabled={!f.enabled} onChange={(e) => setFollow({ message: e.target.value })} aria-label="Follow-up message" />
                </span>
                <span className="remind-line">
                  <span className="muted">If nobody does, remind again{f.remindMinutes ? ' after' : ''}</span>
                  <select className="select select--sm remind-select" aria-label="Remind again after" value={f.remindMinutes ? String(f.remindMinutes) : ''} onChange={(e) => setFollow({ remindMinutes: e.target.value ? Number(e.target.value) : null })}>
                    {/* An interval set elsewhere (the API) still shows as itself. */}
                    {(f.remindMinutes && !REMIND.some(([v]) => v === String(f.remindMinutes)) ? [...REMIND, [String(f.remindMinutes), `${f.remindMinutes} minutes`]] : REMIND).map(([v, label]) => (
                      <option key={v} value={v}>
                        {label}
                      </option>
                    ))}
                  </select>
                  {f.remindMinutes ? (
                    <span className="remind-times">
                      <span className="muted">up to</span>
                      <select className="select select--sm remind-select" aria-label="How many times" value={String(f.remindTimes || 3)} onChange={(e) => setFollow({ remindTimes: Number(e.target.value) })}>
                        {REMIND_TIMES.map((t) => (
                          <option key={t} value={t}>
                            {t}
                          </option>
                        ))}
                      </select>
                      <span className="muted">{(f.remindTimes || 3) === 1 ? 'time' : 'times'}</span>
                    </span>
                  ) : null}
                </span>
              </div>
            </div>
          ) : null}
        </div>
      }
    >
      Send a notification{snapshot && n.snapshot !== false ? ' with a snapshot' : ''} to
      {people.data === undefined ? (
        <span className="muted">loading…</span>
      ) : !list.length ? (
        <a href="#/household">add people first</a>
      ) : (
        <span className="people-inline" role="group" aria-label="Who gets it">
          {list.map((p) => {
            const on = auto.recipients.includes(p.id);
            return (
              <button key={p.id} type="button" role="checkbox" aria-checked={on} className="person-inline" onClick={() => togglePerson(p.id)} title={(p.phones || []).map((ph) => ph.name || platformLabel(ph.platform)).join(', ') || 'No phones yet'}>
                <span className="avatar avatar--sm" aria-hidden="true">
                  {on ? <Icon name="check" size={12} strokeWidth={3} /> : initial(p.name)}
                </span>
                {p.name}
              </button>
            );
          })}
        </span>
      )}
    </Row>
  );
}

const WALL_STYLES = [
  { id: 'card', label: 'Card', hint: 'slides in at the top with the camera, then goes away by itself' },
  { id: 'full', label: 'Full screen', hint: 'the camera live and full screen, back to the dashboard after a minute' },
  { id: 'alarm', label: 'Alarm', hint: 'full-screen red, readable across the room, until someone taps OK' },
];

/** "Show it on the wall tablets": which tablets (none = every one), how, sound and wake. */
function WallRow({ auto, patch, tablets }) {
  const w = auto.wall;
  const setWall = (p) => patch((a) => ({ ...a, wall: { ...a.wall, ...p } }));
  const list = tablets.data || [];
  const picked = (w.tablets || []).filter((id) => list.some((tb) => tb.id === id));
  const toggle = (id) => setWall({ tablets: picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id] });
  const style = WALL_STYLES.find((s) => s.id === w.style) || WALL_STYLES[0];
  return (
    <Row
      tag="Wall"
      removeLabel="Don't show it on wall tablets"
      onRemove={() => setWall({ enabled: false })}
      sub={
        <div className="notify-sub">
          <div className="nsub">
            <span className="nsub-label">Tablets</span>
            <div className="nsub-checks">
              {tablets.data === undefined ? (
                <span className="muted">loading…</span>
              ) : !list.length ? (
                <span className="muted">
                  Add a wall tablet account under <a href="#/household/accounts">Accounts</a>.
                </span>
              ) : (
                <>
                  {list.map((tb) => (
                    <Check key={tb.id} checked={picked.includes(tb.id)} onChange={() => toggle(tb.id)}>
                      {tb.name}
                    </Check>
                  ))}
                  <span className="muted small">{picked.length ? '' : 'None ticked: every wall tablet.'}</span>
                </>
              )}
            </div>
          </div>
          <div className="nsub">
            <span className="nsub-label">How</span>
            <div className="nsub-checks nsub-checks--col" role="radiogroup" aria-label="How it shows">
              {WALL_STYLES.map((s) => (
                <button key={s.id} type="button" role="radio" aria-checked={style.id === s.id} className="check" onClick={() => setWall({ style: s.id })}>
                  <span className="box box--round" aria-hidden="true">
                    {style.id === s.id ? <span className="box-dot" /> : null}
                  </span>
                  <span>
                    <strong>{s.label}</strong> <span className="muted">— {s.hint}</span>
                  </span>
                </button>
              ))}
            </div>
          </div>
          <div className="nsub">
            <span className="nsub-label">Options</span>
            <div className="nsub-checks">
              <Switch checked={w.sound !== false} onChange={(v) => setWall({ sound: v })} label="Play a sound" />
              <Switch checked={w.wake !== false} onChange={(v) => setWall({ wake: v })} label="Wake the screen" />
            </div>
          </div>
        </div>
      }
    >
      Show it on {picked.length ? list.filter((tb) => picked.includes(tb.id)).map((tb) => tb.name).join(' and ') : 'every wall tablet'} as {style.id === 'card' ? 'a card' : style.id === 'full' ? 'full screen' : 'an alarm'}
    </Row>
  );
}

const LIVE_UNTIL = [
  { id: 'leaves', label: 'until they’re no longer…' },
  { id: 'duration', label: 'for a while' },
  { id: 'trigger', label: 'until something else happens' },
];
const UNTIL_ITEMS = [
  { id: 'state', label: 'a device changes' },
  { id: 'event', label: 'a device does something' },
  { id: 'time', label: 'it’s a set time' },
];

/** What ends the live status when "something else happens": a device state, a device event or a time. */
function UntilTrigger({ item, onChange, hub }) {
  const kind = item.type === 'state' ? kindOfIds(item.entities || [], hub) : null;
  const k = kind && hub.kindsById[kind];
  const e = item.type === 'event' ? hub.entitiesById[item.entity] : null;
  const evs = e ? eventsFor(e, hub.kindsById[e.kind]) : [];
  return (
    <span className="blank-seq">
      <select className="blank blank--select" aria-label="What ends it" value={item.type} onChange={(ev) => onChange(newTriggerItem(ev.target.value))}>
        {UNTIL_ITEMS.map((o) => (
          <option key={o.id} value={o.id}>
            {o.label}
          </option>
        ))}
      </select>
      {item.type === 'state' ? (
        <>
          <DeviceBlanks
            ids={item.entities || []}
            include={(d, dk) => !!(dk && (dk.states || []).length > 1) && (!kind || d.kind === kind)}
            emptyLabel="Choose devices"
            onChange={(ids) => {
              const nk = hub.kindsById[kindOfIds(ids, hub)];
              const keep = nk && (nk.states || []).some((s) => s.id === item.to);
              onChange({ ...item, entities: ids, to: keep ? item.to : 'any' });
            }}
          />
          {(item.entities || []).length ? (
            <select className="blank blank--select" aria-label="To what" value={item.to || 'any'} onChange={(ev) => onChange({ ...item, to: ev.target.value })}>
              <option value="any">changes at all</option>
              {((k && k.states) || []).map((s) => (
                <option key={s.id} value={s.id}>
                  becomes {s.label.toLowerCase()}
                </option>
              ))}
            </select>
          ) : null}
        </>
      ) : null}
      {item.type === 'event' ? (
        <>
          <DeviceBlanks
            single
            ids={item.entity ? [item.entity] : []}
            emptyLabel="Choose a device"
            include={(d, dk) => eventsFor(d, dk).length > 0}
            onChange={(ids) => {
              const ne = hub.entitiesById[ids[0]];
              const nevs = ne ? eventsFor(ne, hub.kindsById[ne.kind]) : [];
              onChange({ ...item, entity: ids[0] || '', event: nevs.some((x) => x.id === item.event) ? item.event : nevs.length === 1 ? nevs[0].id : '' });
            }}
          />
          {e && evs.length > 1 ? (
            <select className={'blank blank--select' + (item.event ? '' : ' blank--empty')} aria-label="What it does" value={item.event || ''} onChange={(ev) => onChange({ ...item, event: ev.target.value })}>
              {!item.event ? (
                <option value="" disabled>
                  Choose what happens…
                </option>
              ) : null}
              {evs.map((x) => (
                <option key={x.id} value={x.id}>
                  {x.label}
                </option>
              ))}
            </select>
          ) : e && evs.length === 1 ? (
            <span>{evs[0].label}</span>
          ) : null}
        </>
      ) : null}
      {item.type === 'time' ? (
        <>
          <input type="time" className="blank blank--input" aria-label="Time that ends it" value={item.time || ''} onChange={(ev) => ev.target.value && onChange({ ...item, time: ev.target.value })} />
          on
          <DaysSelect days={item.days || []} onChange={(days) => onChange({ ...item, days })} />
        </>
      ) : null}
    </span>
  );
}

/** "Show a live status": which devices, to whom, until when, and its title. */
function LiveRow({ auto, patch, hub, people }) {
  const lv = auto.live;
  const setLive = (p) => patch((a) => ({ ...a, live: { ...a.live, ...p } }));
  const setUntil = (u) => setLive({ until: cleanUntil(u) });
  const u = cleanUntil(lv.until);
  const list = people.data || [];
  const everyone = lv.recipients === null || lv.recipients === undefined;
  const picked = everyone ? [] : lv.recipients;
  const togglePerson = (id) => setLive({ recipients: picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id] });
  const ents = (lv.entities || []).map((id) => hub.entitiesById[id]).filter(Boolean);
  const kind = kindOfIds(lv.entities || [], hub);
  const k = kind && hub.kindsById[kind];
  // "No longer …": each device's own active state, or (one kind of device) one of that kind's states.
  const word = (ents.length && ents.every((e) => e.kind === ents[0].kind) && activeLabel(hub.kindsById[ents[0].kind])) || 'on';
  const states = k ? stateChoices(k) : [];
  return (
    <Row
      tag="Live"
      removeLabel="Don't show a live status"
      onRemove={() => setLive({ enabled: false })}
      sub={
        <div className="notify-sub">
          <div className="nsub">
            <span className="nsub-label">Until</span>
            <div className="nsub-checks">
              <select className="blank blank--select" aria-label="How long it shows" value={u.type} onChange={(e) => setUntil({ type: e.target.value, ...(e.target.value === 'trigger' ? { item: newTriggerItem('state') } : {}) })}>
                {LIVE_UNTIL.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.label}
                  </option>
                ))}
              </select>
              {u.type === 'leaves' ? (
                <select className="blank blank--select" aria-label="No longer in which state" value={u.state} onChange={(e) => setUntil({ ...u, state: e.target.value })}>
                  <option value="active">{word.toLowerCase()}</option>
                  {states
                    .filter((s) => !k || s.id !== k.activeState)
                    .map((s) => (
                      <option key={s.id} value={s.id}>
                        {s.label.toLowerCase()}
                      </option>
                    ))}
                </select>
              ) : null}
              {u.type === 'duration' ? (
                <span className="blank-seq">
                  <input
                    type="number"
                    min={1}
                    className="blank blank--input blank--num"
                    aria-label="For how long"
                    value={u.amount ?? ''}
                    onChange={(e) => setUntil({ ...u, amount: e.target.value === '' ? '' : Number(e.target.value) })}
                  />
                  <select className="blank blank--select" aria-label="Unit" value={u.unit} onChange={(e) => setUntil({ ...u, unit: e.target.value })}>
                    {HOLD_UNITS.map((x) => (
                      <option key={x} value={x}>
                        {x}
                      </option>
                    ))}
                  </select>
                </span>
              ) : null}
              {u.type === 'trigger' ? <UntilTrigger item={u.item} hub={hub} onChange={(item) => setUntil({ ...u, item })} /> : null}
            </div>
          </div>
          <div className="nsub">
            <span className="nsub-label">Who sees it</span>
            <div className="nsub-checks">
              <Check checked={everyone} onChange={(v) => setLive({ recipients: v ? null : list.map((p) => p.id) })}>
                Everyone
              </Check>
              {!everyone
                ? list.map((p) => (
                    <Check key={p.id} checked={picked.includes(p.id)} onChange={() => togglePerson(p.id)}>
                      {p.name}
                    </Check>
                  ))
                : null}
            </div>
          </div>
          <div className="nsub">
            <span className="nsub-label">Options</span>
            <div className="nsub-checks">
              <label className="inline-form nsub-title">
                <span className="muted">Title</span>
                <input className="input input--sm" value={lv.title || ''} placeholder={ents.map((e) => e.name).join(', ') || auto.name} onChange={(e) => setLive({ title: e.target.value })} />
              </label>
            </div>
          </div>
        </div>
      }
    >
      Show a live status of
      <DeviceBlanks ids={lv.entities || []} joiner="and" emptyLabel="Choose devices" onChange={(ids) => setLive({ entities: ids })} />
      on {everyone ? 'everyone’s' : picked.length ? list.filter((p) => picked.includes(p.id)).map((p) => p.name + '’s').join(' and ') : 'nobody’s'} phones
    </Row>
  );
}

function AutoCommandRow({ auto, patch, hub }) {
  const ac = auto.autoCommand;
  const cmds = availableCommands(auto, hub);
  const word = activeWord(auto, hub);
  return (
    <Row tag="Also" removeLabel="Stop doing it automatically" onRemove={() => patch((a) => ({ ...a, autoCommand: { ...a.autoCommand, enabled: false } }))}>
      Automatically
      <select className="blank blank--select blank--strong" aria-label="Command" value={ac.command} onChange={(e) => patch((a) => ({ ...a, autoCommand: { ...a.autoCommand, command: e.target.value } }))}>
        {cmds.map((c) => (
          <option key={c.id} value={c.id}>
            {c.label.toLowerCase()}
          </option>
        ))}
      </select>
      the devices that are still {word === 'on' ? 'pending' : word}
    </Row>
  );
}

/** The devices a new live status starts with: the ones its "When" watches for a state. */
function liveStart(a) {
  return [...new Set(triggerItems(a.trigger).filter((i) => i.type === 'state').flatMap((i) => i.entities || []))];
}

function ThenSection({ auto, patch, hub, people, choices }) {
  const actions = auto.actions || [];
  const tablets = useApi('api/tablets');
  const setAction = (i, next) => patch((a) => ({ ...a, actions: a.actions.map((x, j) => (j === i ? next : x)) }));
  const cmds = availableCommands(auto, hub);
  const items = [
    { label: 'Control devices (turn on, lock, set…)', onClick: () => patch((a) => ({ ...a, actions: [...(a.actions || []), { entities: [], command: '' }] })) },
    ...(auto.notify.enabled ? [] : [{ label: 'Send a notification', onClick: () => patch((a) => ({ ...a, notify: { ...a.notify, enabled: true } })) }]),
    ...(auto.wall && auto.wall.enabled ? [] : [{ label: 'Show on wall tablets', onClick: () => patch((a) => ({ ...a, wall: { ...a.wall, enabled: true } })) }]),
    ...(auto.live && auto.live.enabled
      ? []
      : [
          {
            label: 'Show a live status on phones',
            // Starts with the devices the automation watches (if any).
            onClick: () => patch((a) => ({ ...a, live: { ...a.live, enabled: true, entities: a.live && a.live.entities && a.live.entities.length ? a.live.entities : liveStart(a) } })),
          },
        ]),
    ...(cmds.length && !auto.autoCommand.enabled
      ? [
          {
            label: 'Handle pending devices automatically',
            onClick: () => patch((a) => ({ ...a, autoCommand: { enabled: true, command: (cmds.find((c) => !c.sensitive) || cmds[0]).id } })),
          },
        ]
      : []),
  ];
  const empty = !actions.length && !auto.notify.enabled && !auto.autoCommand.enabled && !(auto.wall && auto.wall.enabled) && !(auto.live && auto.live.enabled);
  return (
    <section className="bsec" aria-labelledby="sec-then">
      <div className="bsec-head">
        <h2 id="sec-then" className="bsec-title">
          What happens
        </h2>
        {empty ? <span className="bsec-hint amber">Add at least one thing that happens.</span> : null}
      </div>
      {actions.map((x, i) => (
        <ActionRow key={i} idx={i} action={x} hub={hub} onChange={(next) => setAction(i, next)} onRemove={() => patch((a) => ({ ...a, actions: a.actions.filter((_, j) => j !== i) }))} />
      ))}
      {auto.notify.enabled ? <NotifyRow auto={auto} patch={patch} hub={hub} people={people} choices={choices} /> : null}
      {auto.wall && auto.wall.enabled ? <WallRow auto={auto} patch={patch} tablets={tablets} /> : null}
      {auto.live && auto.live.enabled ? <LiveRow auto={auto} patch={patch} hub={hub} people={people} /> : null}
      {auto.autoCommand.enabled ? <AutoCommandRow auto={auto} patch={patch} hub={hub} /> : null}
      <div className="add-row">
        <AddMenu items={items} />
      </div>
    </section>
  );
}

/** "+ Add something that happens" with its small menu. */
function AddMenu({ items, label = '+ Add something that happens' }) {
  const [open, setOpen] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    if (!open) return undefined;
    const onDoc = (e) => ref.current && !ref.current.contains(e.target) && setOpen(false);
    const onKey = (e) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('keydown', onKey);
    };
  }, [open]);
  return (
    <div className="more" ref={ref}>
      <button type="button" className="add-btn" aria-haspopup="menu" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        {label}
      </button>
      {open ? (
        <div className="more-menu more-menu--left" role="menu">
          {items.map((it) => (
            <button
              key={it.label}
              type="button"
              role="menuitem"
              className="more-item"
              onClick={() => {
                setOpen(false);
                it.onClick();
              }}
            >
              {it.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- Page ---------------- */

/** The hub's sentence split into clauses, each linked to the section it describes. */
function sentenceClauses(sentence) {
  let prev = '';
  return sentence.split(/(?<=[,.])\s+/).map((text) => {
    const s = text.trim().toLowerCase();
    let step = 'then';
    if (/^(at |when |every |on |whenever )/.test(s)) step = 'when';
    else if (/^(if |only |but only)/.test(s) && !/^if not/.test(s)) step = 'if';
    // "When it's 10 PM, a cat is detected on Garage or …": a "When" list goes on over commas.
    else if (prev === 'when' && !/^(turn|open|close|lock|unlock|set|arm|disarm|notify|dim|start|stop|show|once|do nothing)\b/.test(s)) step = 'when';
    prev = step;
    return { text, step };
  });
}

function runLine(r) {
  const log = r.log || [];
  const last = log.length ? log[log.length - 1].text : '';
  const status = { done: 'ran', resolved: 'done', sent: 'sent', skipped: 'skipped', failed: 'failed', dismissed: 'dismissed' }[r.status] || r.status;
  return { time: fmtWhen(r.startedAt) || fmtClock(r.startedAt), status, text: last };
}

export default function AutomationEditor({ id, templateId, query }) {
  const hub = useHub();
  const hubRef = useRef(hub);
  hubRef.current = hub;
  const startEntity = query ? query.get('entity') : null;
  const startEvent = query ? query.get('event') : null;
  const startTrigger = query ? query.get('trigger') : null;
  const startProperty = query ? query.get('property') : null;
  const confirm = useConfirm();
  const { isAdmin } = useAuth();
  const isNew = id === 'new';
  const people = useApi('api/people');
  const runs = useApi(isNew ? null : 'api/runs?automationId=' + enc(id));
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && ['automation', 'followup', 'command'].includes(m.entry.kind), () => runs.reload(), 500);
  const sections = { when: useRef(null), if: useRef(null), then: useRef(null) };

  const [auto, setAuto] = useState(null);
  const [savedJson, setSavedJson] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [desc, setDesc] = useState(null);
  const [descState, setDescState] = useState('idle');
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  // One save at a time, and a new automation is only ever created once: after the first
  // save everything (including Test run) targets the id it got. (Hooks: declared before any early return.)
  const savingRef = useRef(false);
  const createdIdRef = useRef(null);

  const load = useCallback(async () => {
    setLoadError(null);
    try {
      let a;
      if (isNew) {
        if (templateId) {
          const templates = await api.get('api/automations/templates', { quiet: true });
          const t = (templates || []).find((x) => x.id === templateId);
          if (!t) throw new ApiError('That recipe is no longer available.', 404);
          a = normalize({ name: t.name, ...(t.automation || {}) });
          delete a.id;
        } else if (startEntity) {
          const h = hubRef.current;
          const ent = h.entitiesById[startEntity];
          const kind = ent && h.kindsById[ent.kind];
          if (startEvent) {
            const ev = eventsFor(ent, kind).find((x) => x.id === startEvent);
            a = normalize({
              ...newAutomation(),
              name: `${ent ? ent.name : 'Device'}: ${ev ? ev.label : startEvent}`,
              trigger: { type: 'event', entity: startEntity, event: startEvent },
              condition: { devices: [], between: null, days: null, dates: null },
              followUp: { enabled: false, message: newAutomation().followUp.message, remindMinutes: null, remindTimes: 3 },
              // A button press usually does something rather than notifying: start with one empty action.
              notify: { ...newAutomation().notify, enabled: false },
              actions: [{ entities: [], command: '' }],
            });
          } else if (startProperty) {
            // "Create automation with this value" from a device page.
            const base = newAutomation();
            const p = findProp([startEntity], startProperty, h);
            a = { ...base, name: `${ent ? ent.name : 'Device'} ${p ? p.label.toLowerCase() : 'value'}`, trigger: { ...base.trigger, items: [{ type: 'property', entities: [startEntity], property: startProperty, ...propertyStart(p, true) }] } };
          } else {
            const base = newAutomation();
            const first = newTriggerItem(startTrigger === 'time' ? 'time' : 'state');
            a = setTargets({ ...base, trigger: { ...base.trigger, items: [first] } }, [startEntity], h);
            a = { ...a, name: `${ent ? ent.name : 'Device'} ` + (startTrigger === 'time' ? 'check' : 'changes') };
          }
        } else {
          // A blank one: one empty "Do" row to fill in, and no notification until it's added.
          const base = newAutomation();
          a = { ...base, notify: { ...base.notify, enabled: false }, actions: [{ entities: [], command: '' }] };
        }
        setSavedJson(null);
      } else {
        a = normalize(await api.get('api/automations/' + enc(id), { quiet: true }));
        setSavedJson(JSON.stringify(automationBody(cleanForSend(a))));
      }
      setAuto(a);
    } catch (e) {
      setLoadError(e);
    }
  }, [id, isNew, templateId, startEntity, startEvent, startTrigger, startProperty]);

  const loaded = useRef(false);
  const ready = !startEntity || !!hub.entities;
  useEffect(() => {
    if (ready && !loaded.current) {
      loaded.current = true;
      load();
    }
  }, [ready, load]);

  const bodyJson = auto ? JSON.stringify(automationBody(cleanForSend(auto), { keepId: !isNew })) : '';
  const dirty = !!auto && (isNew || bodyJson !== savedJson);
  // Nothing chosen under "When" yet: the hub would read an empty list as "at 9 PM", so don't ask it.
  const noTrigger = !!auto && !triggerItems(auto.trigger).length;
  const describeJson = noTrigger ? '' : bodyJson;

  useEffect(() => {
    const bodyJson = describeJson;
    if (!bodyJson) return undefined;
    let cancelled = false;
    setDescState('loading');
    const t = setTimeout(async () => {
      try {
        const d = await api.post('api/automations/describe', JSON.parse(bodyJson), { quiet: true });
        if (!cancelled) {
          setDesc(d);
          setDescState('idle');
        }
      } catch (e) {
        if (cancelled) return;
        if (e.data && typeof e.data === 'object' && Array.isArray(e.data.errors)) {
          setDesc((d) => ({ ...(d || {}), ...e.data }));
          setDescState('idle');
        } else setDescState('error');
      }
    }, 250);
    return () => {
      cancelled = true;
      clearTimeout(t);
    };
  }, [describeJson]);

  useEffect(() => {
    if (!dirty) return undefined;
    const on = (e) => {
      e.preventDefault();
      e.returnValue = '';
    };
    window.addEventListener('beforeunload', on);
    return () => window.removeEventListener('beforeunload', on);
  }, [dirty]);

  if (loadError) {
    return (
      <div className="app-page">
        <div className="app-head">
          <button type="button" className="crumb" onClick={() => navigate('automations')}>
            Automations /
          </button>
        </div>
        <ErrorBox error={loadError} onRetry={loadError.status === 404 ? null : load}>
          {loadError.status === 404 ? (isNew ? loadError.message : "This automation doesn't exist any more.") : `Couldn't load this automation: ${loadError.message}`}
        </ErrorBox>
      </div>
    );
  }
  if (!auto || !hub.entities) return <Loading>Loading automation…</Loading>;

  const patch = (fn) => setAuto((a) => fn(a));
  const choices = buttonChoices(auto, hub);
  const summaries = blockSummaries(auto, hub, people.data, choices);
  const errors = noTrigger ? ['choose what starts it'] : (desc && Array.isArray(desc.errors) && desc.errors) || [];
  const nameMissing = !auto.name.trim();
  const canSave = dirty && !saving && !errors.length && !nameMissing;
  const jump = (step) => {
    const el = sections[step] && sections[step].current;
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  const save = async () => {
    if (savingRef.current) return null;
    savingRef.current = true;
    setSaving(true);
    try {
      if (isNew && !createdIdRef.current) {
        const created = await api.post('api/automations', automationBody(cleanForSend(auto), { keepId: false }));
        if (created && created.id) createdIdRef.current = created.id;
        toast(`Saved “${auto.name}”`, 'success');
        setSavedJson(bodyJson);
        if (created && created.id) navigate('automations/' + enc(created.id), { replace: true });
        else navigate('automations');
        return created;
      }
      const targetId = createdIdRef.current || id;
      const saved = await api.put('api/automations/' + enc(targetId), automationBody(cleanForSend({ ...auto, id: targetId })));
      const next = normalize(saved && typeof saved === 'object' && saved.id ? saved : auto);
      setAuto(next);
      setSavedJson(JSON.stringify(automationBody(cleanForSend(next))));
      toast(`Saved “${next.name}”`, 'success');
      return next;
    } catch (e) {
      if (e.data && typeof e.data === 'object' && Array.isArray(e.data.errors)) setDesc((d) => ({ ...(d || {}), errors: e.data.errors }));
      return null;
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  const testRun = async () => {
    let targetId = createdIdRef.current || id;
    if (dirty || isNew) {
      const ok = await save();
      if (!ok) return;
      targetId = ok.id || createdIdRef.current || targetId;
    }
    setTesting(true);
    try {
      const run = await api.post('api/automations/' + enc(targetId) + '/run', { force: true });
      const last = run && run.log && run.log.length ? run.log[run.log.length - 1].text : null;
      toast(`Test run ${run && run.status ? run.status : 'started'}${last ? ' — ' + last : ''}`, run && run.status === 'failed' ? 'error' : 'success');
      runs.reload();
    } catch {
      /* toast shown */
    } finally {
      setTesting(false);
    }
  };

  const remove = async () => {
    const ok = await confirm({ title: `Delete “${auto.name}”?`, body: 'It stops running right away. Past runs stay in the activity log.', confirmLabel: 'Delete', danger: true });
    if (!ok) return;
    try {
      await api.del('api/automations/' + enc(id));
      setSavedJson(bodyJson);
      toast(`Deleted “${auto.name}”`, 'success');
      navigate('automations');
    } catch {
      /* toast shown */
    }
  };

  const back = async () => {
    if (dirty && !(await confirm({ title: 'Discard changes?', body: "You haven't saved this automation.", confirmLabel: 'Discard', danger: true }))) return;
    navigate('automations');
  };

  const acts = (auto.actions || []).map(toMulti).filter((x) => x.entities.length && x.command);
  const whatHappens = [
    { step: 'when', text: summaries.when },
    ...acts.map((x) => ({ step: 'then', text: '→ ' + actionLabel(x, hub) })),
    ...(auto.notify.enabled ? [{ step: 'then', text: '→ Notify ' + (summaries.who || '') }] : []),
    ...(auto.wall && auto.wall.enabled ? [{ step: 'then', text: '→ Show on ' + ((auto.wall.tablets || []).length ? plural(auto.wall.tablets.length, 'wall tablet') : 'every wall tablet') }] : []),
    ...(auto.live && auto.live.enabled ? [{ step: 'then', text: '→ Live status of ' + plural((auto.live.entities || []).length, 'device') }] : []),
    ...(summaries.if && summaries.if !== 'Every time' ? [{ step: 'if', text: summaries.if }] : []),
  ].filter((x) => x.text);
  const runList = (runs.data || []).slice().sort((a, b) => (b.startedAt || 0) - (a.startedAt || 0)).slice(0, 6).map((r) => ({ id: r.id, ...runLine(r) }));

  return (
    <div className="app-page editor3">
      <div className="app-head app-head--nowrap">
        <button type="button" className="crumb" onClick={back}>
          Automations /
        </button>
        <h1 className="sr-only">{isNew ? 'New automation' : 'Edit automation'}</h1>
        <input className="name-input" readOnly={!isAdmin} aria-label="Automation name" value={auto.name} onChange={(e) => patch((a) => ({ ...a, name: e.target.value }))} placeholder="Name this automation" />
        {!auto.enabled ? <span className="pill">Off</span> : null}
        {dirty && !isNew && isAdmin ? <span className="dirty-pill">Unsaved</span> : null}
        {!isAdmin ? <span className="pill" title="Only an admin can change automations">View only</span> : null}
        {isAdmin ? (
        <>
        <button type="button" className="btn head-btn" onClick={testRun} disabled={isNew || testing || saving || (dirty && !canSave)} title={isNew ? 'Save it first, then test it' : 'Runs now and skips the Only-if check'}>
          {testing ? <span className="spinner" style={{ width: 16, height: 16 }} /> : <Icon name="play" size={15} />}
          Test run
        </button>
        <button type="button" className="btn btn-primary head-btn" onClick={save} disabled={!canSave}>
          {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
        </button>
        <MoreMenu
          label="More actions"
          items={[
            { label: auto.enabled ? 'Turn off (keep it, but don’t run it)' : 'Turn on', onClick: () => patch((a) => ({ ...a, enabled: !a.enabled })) },
            ...(isNew ? [] : [{ label: 'Delete automation', danger: true, onClick: remove }]),
          ]}
        />
        </>
        ) : null}
      </div>

      <section className="sentence sentence--fixed" aria-live="polite" aria-label="In plain English">
        <p className={'sentence-text' + (descState === 'loading' && desc ? ' is-stale' : '')}>
          {noTrigger
            ? 'Choose what starts it — a time, a device, or something that happens.'
            : desc && desc.summary
            ? sentenceClauses(desc.summary).map((cl, i) => (
                <span key={i}>
                  {i ? ' ' : ''}
                  <button type="button" className="clause" onClick={() => jump(cl.step)}>
                    {cl.text}
                  </button>
                </span>
              ))
            : descState === 'error'
              ? "Couldn't reach the hub to describe this automation."
              : 'Describing…'}
        </p>
        <div className={'sentence-errors' + (errors.length || nameMissing ? '' : ' is-empty')} role={errors.length || nameMissing ? 'alert' : undefined}>
          {errors.length || nameMissing ? (
            <>
              <Icon name="alert" size={15} /> Before you can save: {[...(nameMissing ? ['give it a name'] : []), ...errors].join(' · ')}
            </>
          ) : (
            <>
              <Icon name="check" size={15} /> Ready to save
            </>
          )}
        </div>
      </section>

      <div className="editor-main">
        <div className="pane builder-pane">
          {/* Family accounts can read automations but not change them. */}
          <fieldset className="builder-scroll builder-fieldset" disabled={!isAdmin}>
            <div ref={sections.when}>
              <WhenSection auto={auto} patch={patch} hub={hub} />
            </div>
            <div ref={sections.then}>
              <ThenSection auto={auto} patch={patch} hub={hub} people={people} choices={choices} />
            </div>
            <div ref={sections.if}>
              <OnlyIfSection key={triggerKind(auto.trigger) === 'time' ? 'time' : 'other'} auto={auto} patch={patch} hub={hub} />
            </div>
          </fieldset>
        </div>

        <aside className="editor-side">
          <section className="pane side-card" aria-labelledby="wwh">
            <h2 id="wwh" className="eyebrow eyebrow--sm">
              What will happen
            </h2>
            <ol className="wwh">
              {whatHappens.map((x, i) => (
                <li key={i}>
                  <button type="button" className="wwh-row" onClick={() => jump(x.step)}>
                    <span className="wwh-num">{i + 1}</span>
                    <span>{x.text}</span>
                  </button>
                </li>
              ))}
            </ol>
          </section>
          <section className="pane side-card side-runs" aria-labelledby="lastruns">
            <h2 id="lastruns" className="eyebrow eyebrow--sm">
              Last runs
            </h2>
            {isNew ? (
              <div className="help">Runs show up here once it’s saved.</div>
            ) : runs.data === undefined ? (
              <Loading />
            ) : !runList.length ? (
              <div className="help">It hasn’t run yet. Use “Test run” to try it.</div>
            ) : (
              <ul className="runlines">
                {runList.map((r) => (
                  <li key={r.id} className={'runline runline--' + r.status}>
                    <b>{r.time}</b> · {r.status}
                    {r.text ? <span className="muted"> · {r.text}</span> : null}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </aside>
      </div>
    </div>
  );
}

