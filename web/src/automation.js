// Editing helpers for the automation builder. The full plain-English sentence
// and the phone preview come from POST /api/automations/describe; the short
// per-block summaries below are only UI labels for the step cards.

import { daysText, fmtHHMM, joinList } from './format.js';
import { actionCommands, activeLabel, commandCurrent, eventsFor, stateChoices, stateLabelOf, valueText } from './model.js';

export const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];
export const DEFAULT_FOLLOW_MESSAGE = '{person} {action} {devices}.';
export const DEFAULT_WITHIN = { amount: 2, unit: 'minutes' };
export const WITHIN_UNITS = ['seconds', 'minutes', 'hours'];

export function newAutomation() {
  return {
    name: 'New automation',
    enabled: true,
    // "When" starts empty: the first row asks what kind of thing starts it.
    trigger: { match: 'any', within: { ...DEFAULT_WITHIN }, items: [] },
    condition: { devices: [], between: null, days: null, dates: null },
    notify: { enabled: true, title: '', buttons: [], onlyPending: true, timeSensitive: false, snapshot: true },
    actions: [],
    autoCommand: { enabled: false, command: '' },
    recipients: [],
    followUp: { enabled: false, message: DEFAULT_FOLLOW_MESSAGE, remindMinutes: null, remindTimes: 3 },
    // Show it on wall tablets: tablets [] = every wall tablet; style card | full | alarm.
    wall: { enabled: false, tablets: [], style: 'card', sound: true, wake: true },
    // Show a live status (iPhone Live Activity / Android ongoing notification) of devices to people
    // (recipients null = everyone) until: they leave a state | a duration | something else happens.
    live: { enabled: false, entities: [], title: '', recipients: null, until: { type: 'leaves', entities: [], state: 'active' } },
  };
}

/** A live status's "until", every field there (the hub's describe.clean_live). */
export function cleanUntil(u) {
  const x = u || {};
  if (x.type === 'duration') return { type: 'duration', amount: x.amount ?? 30, unit: x.unit || 'minutes' };
  if (x.type === 'trigger') return { type: 'trigger', item: itemOf(x.item || { type: 'state' }) };
  return { type: 'leaves', entities: [...(x.entities || [])], state: x.state || 'active' };
}

/** Fill in any missing sections so the editor can rely on the shape. */
export function normalize(a) {
  const d = newAutomation();
  const src = a || {};
  const out = {
    ...d,
    ...src,
    trigger: { ...(src.trigger || d.trigger) },
    // No default `devices` here: older automations only have flat entities + mode, read by conditionRows.
    condition: { between: null, days: null, ...(src.condition || {}) },
    notify: { ...d.notify, ...(src.notify || {}) },
    autoCommand: { ...d.autoCommand, ...(src.autoCommand || {}) },
    followUp: { ...d.followUp, ...(src.followUp || {}) },
    wall: { ...d.wall, ...(src.wall || {}), tablets: [...((src.wall && src.wall.tablets) || [])] },
    live: {
      ...d.live,
      ...(src.live || {}),
      entities: [...((src.live && src.live.entities) || [])],
      recipients: src.live && Array.isArray(src.live.recipients) ? [...src.live.recipients] : null,
      until: cleanUntil(src.live && src.live.until),
    },
    recipients: [...(src.recipients || [])],
    // Older actions had one "entity"; the editor always works with "entities".
    actions: Array.isArray(src.actions) ? src.actions.map(toMulti) : [],
  };
  if (out.condition.between === undefined) out.condition.between = null;
  if (out.condition.days === undefined) out.condition.days = null;
  out.trigger = cleanTrigger(out.trigger);
  out.condition = { between: out.condition.between, days: out.condition.days, sun: cleanSun(out.condition.sun), dates: cleanDates(out.condition.dates), devices: conditionRows(out.condition, out.trigger) };
  out.notify.buttons = [...(out.notify.buttons || [])];
  if (out.followUp.message === undefined || out.followUp.message === null) out.followUp.message = DEFAULT_FOLLOW_MESSAGE;
  return out;
}

const MATCHES = ['any', 'all', 'none', 'always'];

/** A fresh "When" part of one kind. */
export function newTriggerItem(type) {
  if (type === 'time') return { type: 'time', time: '21:00', days: ALL_DAYS.slice() };
  if (type === 'event') return { type: 'event', entity: '', event: '' };
  if (type === 'property') return { type: 'property', entities: [], property: '', op: '', value: null };
  return { type: 'state', entities: [], to: 'any' };
}

export const SUN_EVENTS = ['sunrise', 'sunset', 'dawn', 'dusk'];
const SUN_WORDS = { sunrise: 'sunrise', sunset: 'sunset', dawn: 'dawn', dusk: 'dusk' };

/** 30 -> "30 min", 60 -> "1 hour", 90 -> "1 h 30 min" (sign ignored). */
export function offsetText(min) {
  const m = Math.abs(Math.round(Number(min) || 0));
  if (m < 60) return `${m} min`;
  const h = Math.floor(m / 60);
  const r = m % 60;
  return r ? `${h} h ${r} min` : `${h} hour${h > 1 ? 's' : ''}`;
}

/** "sunset", "30 min before sunrise" (the hub's describe words it the same way). */
export function sunMoment(event, offset) {
  const w = SUN_WORDS[event] || event;
  const o = Math.round(Number(offset) || 0);
  return o ? `${offsetText(o)} ${o < 0 ? 'before' : 'after'} ${w}` : w;
}

/** The "only when it's dark / during daylight" check: {only: dark|light, sunset, sunrise} or null. */
export function cleanSun(s) {
  if (!s || (s.only !== 'dark' && s.only !== 'light')) return null;
  return { only: s.only, sunset: Math.round(Number(s.sunset) || 0), sunrise: Math.round(Number(s.sunrise) || 0) };
}

export function sunCondText(s0) {
  const s = cleanSun(s0);
  if (!s) return '';
  if (s.only === 'light') return !s.sunrise && !s.sunset ? 'during daylight' : `between ${sunMoment('sunrise', s.sunrise)} and ${sunMoment('sunset', s.sunset)}`;
  return `between ${sunMoment('sunset', s.sunset)} and ${sunMoment('sunrise', s.sunrise)}`;
}

function timeWords(i) {
  return i.sun ? sunMoment(i.sun, i.offset) : fmtHHMM(i.time);
}

function itemOf(i) {
  const type = i.type || ('event' in i ? 'event' : 'time' in i ? 'time' : 'state');
  if (type === 'time') {
    const out = { type, time: i.time || '21:00', days: Array.isArray(i.days) ? [...i.days].sort() : ALL_DAYS.slice() };
    // "At sunset" / "30 min before sunrise": follows the sun instead of the clock.
    return SUN_EVENTS.includes(i.sun) ? { ...out, sun: i.sun, offset: Math.round(Number(i.offset) || 0) } : out;
  }
  // No default event: the hub asks "Pick what X has to do" until one is chosen.
  if (type === 'event') return { type, entity: i.entity || '', event: i.event || '' };
  // A value a device reports (temperature, battery…) compared with op/value.
  const hold = i.for && typeof i.for === 'object' ? { for: { amount: i.for.amount ?? '', unit: i.for.unit || 'minutes' } } : {};
  if (type === 'property') return { type, entities: [...(i.entities || [])], property: i.property || '', op: i.op || '', value: i.value === undefined ? null : i.value, ...hold };
  return { type: 'state', entities: [...(i.entities || [])], to: i.to || 'any', ...hold };
}

/** A When part can be held "for how long" when it names a state (not "any") or compares a value (not "changes"). */
export function canHold(i) {
  if (!i) return false;
  if (i.type === 'state') return !!i.to && i.to !== 'any';
  if (i.type === 'property') return !!i.op && i.op !== 'changes';
  return false;
}

/** What the hub gets for a part's "for": dropped when empty or not allowed; otherwise {amount, unit} as typed (the hub checks it). */
function holdForSend(i) {
  // eslint-disable-next-line no-unused-vars
  const { for: hold, ...rest } = i;
  if (!hold || !canHold(i) || hold.amount === '' || hold.amount === null || hold.amount === undefined) return rest;
  return { ...rest, for: { amount: Number(hold.amount), unit: hold.unit || 'minutes' } };
}

/**
 * The "When" parts: [{type: time|state|event, …}], mixed freely. `items`
 * wins; older automations are one flat time/state/event trigger (one part).
 * An empty list means nothing has been chosen yet.
 */
export function triggerItems(t) {
  const src = t || {};
  if (Array.isArray(src.items) && src.items.length) return src.items.map(itemOf);
  if (Array.isArray(src.items) || !src.type || src.type === 'mixed') return [];
  return [itemOf({ ...src })];
}

/** Only what the hub should get: match, within, items (never the derived type or flat fields). */
function cleanTrigger(t) {
  const w = (t && t.within) || {};
  return {
    match: t && t.match === 'all' ? 'all' : 'any',
    within: { amount: w.amount === undefined || w.amount === null || w.amount === '' ? DEFAULT_WITHIN.amount : Number(w.amount), unit: WITHIN_UNITS.includes(w.unit) ? w.unit : DEFAULT_WITHIN.unit },
    items: triggerItems(t).map(holdForSend),
  };
}

/** "time" | "state" | "event" when all parts are alike, "mixed", or "none" (nothing chosen). */
export function triggerKind(t) {
  const types = [...new Set(triggerItems(t).map((i) => i.type))];
  if (!types.length) return 'none';
  return types.length === 1 ? types[0] : 'mixed';
}

/** Devices in the "is / becomes" parts. */
export function stateEntityIds(t) {
  return [...new Set(triggerItems(t).filter((i) => i.type === 'state').flatMap((i) => i.entities))];
}

/**
 * "Only if … is …" rows: [{entities, state, match}]. `devices` wins; older
 * automations have one flat `entities` + `mode` (read as state "active").
 * Old device-change automations repeated the trigger's own devices here with
 * "active" — the hub ignores those, so the editor drops them too.
 */
export function conditionRows(c, trigger) {
  const src = c || {};
  let rows;
  if (Array.isArray(src.devices)) rows = src.devices;
  else rows = (src.entities || []).length ? [{ entities: src.entities, state: 'active', match: src.mode || 'any' }] : [];
  rows = rows.map((r) => {
    const base = { ...r, entities: [...(r.entities || [])], match: MATCHES.includes(r.match) ? r.match : 'any' };
    // A value row carries property (+ op/value) instead of a state.
    if (isValueRow(r)) {
      // eslint-disable-next-line no-unused-vars
      const { state, ...rest } = base;
      return { ...rest, property: r.property || '', op: r.op || '', value: r.value === undefined ? null : r.value };
    }
    return { ...base, state: r.state || 'active' };
  });
  // Only a pure time trigger keeps "always" rows (they are what its notification is about).
  const kind = triggerKind(trigger);
  if (kind !== 'time') {
    const trig = kind === 'state' ? stateEntityIds(trigger) : [];
    rows = rows.filter((r) => r.match !== 'always' || !r.entities.length);
    rows = rows.filter((r) => !(trig.length && r.state === 'active' && r.entities.length && r.entities.every((e) => trig.includes(e))));
  }
  return rows;
}

/** Every device a trigger mentions, states and events (works on hub automations, old or new shape). */
export function triggerEntityIds(t) {
  return [...new Set(triggerItems(t).flatMap((i) => (i.type === 'state' || i.type === 'property' ? i.entities || [] : i.type === 'event' && i.entity ? [i.entity] : [])))];
}

/** Every device the "Only if" rows look at. */
export function conditionEntityIds(c) {
  return [...new Set(conditionRows(c, { type: 'time' }).flatMap((r) => r.entities))];
}

/** Split picked devices into one group per kind, keeping their order. */
export function groupIdsByKind(ids, hub) {
  const groups = [];
  ids.forEach((id) => {
    const e = hub.entitiesById[id];
    const kind = e ? e.kind : '?';
    const g = groups.find((x) => x.kind === kind);
    if (g) g.ids.push(id);
    else groups.push({ kind, ids: [id] });
  });
  return groups;
}

/** The kind shared by a list of devices (null when empty or mixed). */
export function kindOfIds(ids, hub) {
  const kinds = [...new Set(ids.map((i) => hub.entitiesById[i]).filter(Boolean).map((e) => e.kind))];
  return kinds.length === 1 ? kinds[0] : null;
}

/** {entity} -> {entities: [entity]} (old single-device actions). */
export function toMulti(x) {
  // eslint-disable-next-line no-unused-vars
  const { entity, ...rest } = x || {};
  const list = Array.isArray(x && x.entities) ? x.entities.slice() : entity ? [entity] : [];
  return { ...rest, entities: list };
}

/** Value to start with when a command that takes one is picked. */
export function defaultActionValue(cmd, entity, kind) {
  // Driven by the command's valueType and default from /api/meta.
  if (cmd.valueType === 'choices') return Array.isArray(cmd.default) ? cmd.default : [];
  if (cmd.valueType === 'state') {
    const choices = stateChoices(kind);
    if (choices.some((s) => s.id === cmd.default)) return cmd.default;
    return choices[0] ? choices[0].id : '';
  }
  if (cmd.valueType === 'number') {
    // Start from where the device is now, when it says.
    const cur = commandCurrent(entity, cmd);
    if (typeof cur === 'number') return cur;
  }
  if (cmd.default !== undefined && cmd.default !== null) return cmd.default;
  if (cmd.valueType === 'percent' || cmd.valueType === 'number') return typeof cmd.min === 'number' ? cmd.min : 0;
  return '';
}

/** What goes to the hub: drop unfinished action rows and a half-filled time window. */
export function cleanForSend(a) {
  const b = a.condition && a.condition.between;
  // Always match + within + items; never the derived type or the old flat fields.
  const trigger = cleanTrigger(a.trigger);
  // eslint-disable-next-line no-unused-vars
  const { entities: _e, mode: _m, ...cond } = a.condition || {};
  return {
    ...a,
    trigger,
    actions: (a.actions || [])
      .map(toMulti)
      .filter((x) => x.entities.length && x.command)
      .map((x) => {
        // "for" (keep it that way for a while) is only sent when it has a real amount.
        // eslint-disable-next-line no-unused-vars
        const { for: dur, ...rest } = x;
        return dur && Number(dur.amount) > 0 ? { ...rest, for: { amount: Number(dur.amount), unit: dur.unit || 'minutes' } } : rest;
      }),
    condition: {
      ...cond,
      // An "Only if" row nobody picked a device for checks nothing: leave it out.
      devices: conditionRows(a.condition, a.trigger).filter((r) => r.entities.length).map((r) =>
        isValueRow(r) ? { entities: r.entities, property: r.property, op: r.op, value: r.value, match: r.match } : { entities: r.entities, state: r.state, match: r.match },
      ),
      between: b && b.after && b.before ? { after: b.after, before: b.before } : null,
      sun: cleanSun(a.condition.sun),
      dates: cleanDates(a.condition.dates),
      // Day limits don't apply to time triggers (they pick their own days); 7 days = every day.
      days: triggerKind(a.trigger) === 'time' || !Array.isArray(a.condition.days) || a.condition.days.length === 7 || !a.condition.days.length ? null : a.condition.days.slice().sort(),
    },
  };
}

/** Set Kitchen to 80% · Turn on Porch · Lock Side Door */
function forText(x) {
  const d = x && x.for;
  if (!d || !(Number(d.amount) > 0)) return '';
  const n = Number(d.amount);
  const unit = (d.unit || 'minutes').replace(/s$/, '');
  return ` for ${n} ${unit}${n === 1 ? '' : 's'}`;
}

export function actionLabel(x, hub) {
  return baseActionLabel(x, hub) + forText(x);
}

function baseActionLabel(x, hub) {
  const ids = toMulti(x).entities;
  const ents = ids.map((i) => hub.entitiesById[i]);
  const e = ents.find(Boolean);
  const k = e && hub.kindsById[e.kind];
  const c = actionCommands(k).find((y) => y.id === x.command);
  const name = ids.length ? joinList(ents.map((d) => (d ? d.name : 'a missing device')), 'and') : 'a device';
  if (c && c.takesValue) return `${c.label} · ${name}: ${valueText(c, k, x.value, e)}`;
  if (c && c.automationOnly) {
    const [verb, ...rest] = c.label.split(' '); // "Brighten Kitchen while held"
    return [verb, name, ...rest].join(' ');
  }
  return `${c ? c.label : x.command} ${name}`;
}

export const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
/** Days in each month, Feb 29 included (it counts as Feb 28 in other years). */
export const MONTH_DAYS = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];

/** "12-01" -> [12, 1], or null. */
export function parseMMDD(v) {
  const m = /^(\d{1,2})-(\d{1,2})$/.exec(String(v || '').trim());
  return m ? [Number(m[1]), Number(m[2])] : null;
}

export function mmdd(month, day) {
  return `${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
}

/** The "only between dates" limit: {from: "MM-DD", to: "MM-DD", year: null | YYYY}, or null. */
export function cleanDates(x) {
  if (!x || typeof x !== 'object' || (!x.from && !x.to)) return null;
  // A cleared year box stays '' so the hub can ask for one (rather than quietly meaning every year).
  const y = x.year === null || x.year === undefined ? null : x.year === '' ? '' : Number(x.year);
  return { from: x.from || '', to: x.to || '', year: y === null || y === '' || Number.isFinite(y) ? y : String(x.year) };
}

/** "between December 1 and January 6 (every year)" / "from July 1 to July 15, 2027" (the hub words it the same). */
export function datesText(x) {
  const dt = cleanDates(x);
  const a = dt && parseMMDD(dt.from);
  const b = dt && parseMMDD(dt.to);
  if (!a || !b || !MONTHS[a[0] - 1] || !MONTHS[b[0] - 1]) return '';
  const md = (p) => `${MONTHS[p[0] - 1]} ${p[1]}`;
  if (dt.year === null) return `between ${md(a)} and ${md(b)} (every year)`;
  const wraps = b[0] < a[0] || (b[0] === a[0] && b[1] < a[1]);
  return wraps ? `from ${md(a)}, ${dt.year} to ${md(b)}, ${dt.year + 1}` : `from ${md(a)} to ${md(b)}, ${dt.year}`;
}

export function betweenText(b) {
  return b && b.after && b.before ? `between ${fmtHHMM(b.after)} and ${fmtHHMM(b.before)}` : '';
}

/** The devices a run is about (what notification buttons and follow-ups act on). */
export function targetIds(a) {
  // Mirrors the hub: a pure time trigger is about its "Only if" devices; otherwise the "is / becomes" devices.
  const kind = triggerKind(a.trigger);
  if (kind === 'time') return conditionEntityIds(a.condition);
  if (kind === 'state' || kind === 'mixed') return stateEntityIds(a.trigger);
  return [];
}

function uniqCommands(entities, kindsById) {
  const cmds = [];
  entities.forEach((e) => {
    const k = kindsById[e.kind];
    // Commands that need a value (set_brightness) can't be one-tap buttons.
    ((k && k.commands) || []).filter((c) => !c.takesValue).forEach((c) => {
      if (!cmds.some((x) => x.id === c.id)) cmds.push(c);
    });
  });
  return cmds;
}

/** Commands available across the selected devices. */
export function availableCommands(a, hub) {
  const ents = targetIds(a).map((id) => hub.entitiesById[id]).filter(Boolean);
  return uniqCommands(ents, hub.kindsById);
}

function describeUnknownButton(id, hub) {
  let m = /^snooze:(\d+)$/.exec(id);
  if (m) {
    const n = Number(m[1]);
    return { id, label: n % 60 === 0 ? `Snooze ${n / 60} hour${n === 60 ? '' : 's'}` : `Snooze ${n} min`, icon: 'clock' };
  }
  m = /^([^:]+):(.+)$/.exec(id);
  if (m) {
    const e = hub.entitiesById[m[2]];
    const k = e && hub.kindsById[e.kind];
    const c = k && (k.commands || []).find((x) => x.id === m[1]);
    return { id, label: `${c ? c.label : m[1]} ${e ? e.name : '(missing device)'}`, icon: (c && c.icon) || m[1], sensitive: !!(c && c.sensitive) };
  }
  m = /^(.+)_all$/.exec(id);
  if (m) return { id, label: `${m[1].charAt(0).toUpperCase() + m[1].slice(1)} all`, icon: m[1] };
  return { id, label: id, icon: 'dot' };
}

/**
 * Button chips for the Then block:
 * "<cmd>:<entity>" per selected device × command, "<cmd>_all" when ≥2 devices,
 * then Snooze 1 hour, Dismiss, Open Newman. Buttons already on the automation
 * that we wouldn't generate (e.g. "snooze:30" from a recipe) are kept too.
 */
export function buttonChoices(a, hub) {
  const ents = targetIds(a).map((id) => hub.entitiesById[id]).filter(Boolean);
  const out = [];
  uniqCommands(ents, hub.kindsById).forEach((c) => {
    const withCmd = ents.filter((e) => {
      const k = hub.kindsById[e.kind];
      return k && (k.commands || []).some((x) => x.id === c.id && !x.takesValue);
    });
    withCmd.forEach((e) =>
      out.push({ id: `${c.id}:${e.id}`, label: `${c.label} ${e.name}`, icon: c.icon || c.id, sensitive: !!c.sensitive, command: c.id, confirmsTo: c.confirmsTo || '' }),
    );
    if (withCmd.length >= 2) out.push({ id: `${c.id}_all`, label: `${c.label} all`, icon: c.icon || c.id, sensitive: !!c.sensitive, command: c.id, confirmsTo: c.confirmsTo || '' });
  });
  out.push({ id: 'snooze:60', label: 'Snooze 1 hour', icon: 'clock' });
  out.push({ id: 'dismiss', label: 'Dismiss', icon: 'x' });
  out.push({ id: 'open', label: 'Open Newman', icon: 'open' });
  const extra = (a.notify.buttons || []).filter((id) => !out.some((b) => b.id === id)).map((id) => describeUnknownButton(id, hub));
  return [...out, ...extra];
}

export function sortButtons(ids, choices) {
  const order = (id) => {
    const i = choices.findIndex((b) => b.id === id);
    return i < 0 ? 9999 : i;
  };
  return [...new Set(ids)].sort((x, y) => order(x) - order(y));
}

function withTargets(a, ids, hub) {
  const t = a.trigger;
  const kind = triggerKind(t);
  if (kind === 'state') {
    const old = triggerItems(t);
    const groups = groupIdsByKind(ids, hub);
    const items = groups.map((g) => {
      const prev = old.find((i) => kindOfIds(i.entities, hub) === g.kind);
      return { type: 'state', entities: g.ids, to: prev ? prev.to : 'any' };
    });
    return { ...a, trigger: { ...t, items: items.length ? items : [newTriggerItem('state')] } };
  }
  if (kind === 'time') {
    // Pure time triggers: the checked devices are also the ones the notification buttons act on.
    const rows = groupIdsByKind(ids, hub).map((g) => ({ entities: g.ids, state: 'active', match: 'any' }));
    return { ...a, condition: { ...a.condition, devices: rows } };
  }
  return a;
}

/**
 * After the watched devices changed (from `prev` to `next`), keep the
 * notification buttons consistent: drop buttons for devices no longer there
 * (and "_all" below two devices), and give newly added devices the same
 * per-device commands the others have.
 */
export function syncTargets(prev, next0, hub) {
  const before = targetIds(prev);
  const ids = targetIds(next0);
  const added = ids.filter((id) => !before.includes(id));
  let next = next0;
  let buttons = (next0.notify.buttons || []).filter((b) => {
    const m = /^([^:]+):(.+)$/.exec(b);
    if (m && !b.startsWith('snooze:')) return ids.includes(m[2]);
    return true;
  });
  const usedCmds = new Set(
    buttons
      .map((b) => /^([^:]+):(.+)$/.exec(b))
      .filter((m) => m && m[1] !== 'snooze')
      .map((m) => m[1]),
  );
  added.forEach((id) => {
    const e = hub.entitiesById[id];
    const k = e && hub.kindsById[e.kind];
    ((k && k.commands) || []).filter((c) => !c.takesValue).forEach((c) => {
      if (usedCmds.has(c.id)) buttons.push(`${c.id}:${id}`);
    });
  });
  const choices = buttonChoices({ ...next, notify: { ...next.notify, buttons: [] } }, hub);
  buttons = buttons.filter((b) => !/_all$/.test(b) || choices.some((c) => c.id === b));
  next = { ...next, notify: { ...next.notify, buttons: sortButtons(buttons, choices) } };
  const cmds = availableCommands(next, hub);
  if (next.autoCommand.command && !cmds.some((c) => c.id === next.autoCommand.command)) {
    next = { ...next, autoCommand: { ...next.autoCommand, command: '', enabled: false } };
  }
  return next;
}

/** Replace the watched devices with `ids` (grouped into one item/row per kind). */
export function setTargets(a, ids, hub) {
  return syncTargets(a, withTargets(a, ids, hub), hub);
}

/** Apply `fn` to the automation and keep the buttons in step with the devices it now watches. */
export function patchTargets(a, fn, hub) {
  return syncTargets(a, fn(a), hub);
}

/**
 * Short label for an event part: the device and the event's own label from /api/meta
 * ("Garage: Cat detected"). The full sentence wording comes from the hub's describe.
 */
export function eventText(item, hub) {
  const e = hub.entitiesById[item.entity];
  if (!e) return '';
  const k = hub.kindsById[e.kind];
  const ev = item.event ? eventsFor(e, k).find((x) => x.id === item.event) || ((k && k.events) || []).find((x) => x.id === item.event) : null;
  return ev ? `${e.name}: ${ev.label}` : `${e.name}: pick what happens`;
}

function itemText(i, hub, allOf) {
  const base = itemTextBase(i, hub, allOf);
  return base && canHold(i) ? base + forText(i) : base;
}

function itemTextBase(i, hub, allOf) {
  if (i.type === 'property') return propertyText(i, hub, allOf || (canHold(i) && i.for && Number(i.for.amount) > 0) ? 'is' : 'goes');
  if (i.type === 'time') return `it's ${timeWords(i)}${(i.days || []).length === 7 ? '' : ' ' + daysText(i.days)}`;
  if (i.type === 'event') return eventText(i, hub);
  if (!i.entities.length) return '';
  const n = joinList(names(i.entities, hub), allOf ? 'and' : 'or');
  if (i.to === 'any') return `${n} changes`;
  const w = stateWord(i.entities, i.to, hub);
  const held = canHold(i) && i.for && Number(i.for.amount) > 0;
  if (held) return `${n} ${i.entities.length === 1 ? 'stays' : 'stay'} ${w}`;
  return allOf ? `${n} ${i.entities.length === 1 ? 'is' : 'are'} ${w}` : `${n} becomes ${w}`;
}

/** Moments (times, events) are what "within … of each other" applies to. */
export function momentCount(t) {
  return triggerItems(t).filter((i) => i.type === 'time' || i.type === 'event').length;
}

export function withinText(w) {
  const d = w || DEFAULT_WITHIN;
  const n = Number(d.amount);
  const unit = String(d.unit || 'minutes').replace(/s$/, '');
  return `${n} ${unit}${n === 1 ? '' : 's'}`;
}

/** Short label for the When block (the hub's sentence is the real one). */
export function whenText(t, hub) {
  const items = triggerItems(t);
  if (!items.length) return 'Choose what starts it';
  if (items.length === 1 && items[0].type === 'time') return `At ${timeWords(items[0])}, ${daysText(items[0].days)}`;
  const allOf = t.match === 'all';
  const parts = items.map((i) => itemText(i, hub, allOf)).filter(Boolean);
  if (!parts.length) return 'When … — pick a device';
  let s = `When ${joinList(parts, allOf ? 'and' : 'or')}`;
  if (allOf && items.length > 1 && momentCount(t) >= 2) s += ` (within ${withinText(t.within)})`;
  return s;
}

function names(ids, hub) {
  return ids.map((id) => (hub.entitiesById[id] ? hub.entitiesById[id].name : 'a missing device'));
}

/** Label used for "Any is …" chips: the shared activeState label of the selected kinds. */
export function activeWord(a, hub) {
  const kinds = [...new Set(targetIds(a).map((id) => hub.entitiesById[id]).filter(Boolean).map((e) => e.kind))].map((k) => hub.kindsById[k]);
  const labels = [...new Set(kinds.map(activeLabel).filter(Boolean))];
  return labels.length === 1 ? labels[0].toLowerCase() : 'on';
}

export function blockSummaries(a, hub, people, choices) {
  const t = a.trigger;
  const when = whenText(t, hub);

  let onlyIf;
  const win = betweenText(a.condition.between);
  const rows = conditionRows(a.condition, t).filter((r) => r.entities.length);
  const checks = rows.filter((r) => r.match !== 'always').map((r) => rowText(r, hub));
  const told = rows.filter((r) => r.match === 'always').flatMap((r) => r.entities);
  onlyIf = checks.length ? 'If ' + joinList(checks, 'and') : '';
  if (triggerKind(t) === 'time' && told.length) {
    const tell = `tell me how ${joinList(names(told, hub), 'and')} ${told.length === 1 ? 'is' : 'are'}`;
    onlyIf = onlyIf ? `${onlyIf} · ${tell}` : `Every time — ${tell}`;
  }
  if (win) onlyIf = onlyIf ? `${onlyIf} · only ${win}` : `Only ${win}`;
  const sunText = sunCondText(a.condition.sun);
  if (sunText) onlyIf = onlyIf ? `${onlyIf} · only ${sunText}` : `Only ${sunText}`;
  const dtText = datesText(a.condition.dates);
  if (dtText) onlyIf = onlyIf ? `${onlyIf} · only ${dtText}` : `Only ${dtText}`;
  const cd = a.condition.days;
  if (triggerKind(t) !== 'time' && Array.isArray(cd) && cd.length && cd.length < 7) {
    const dt = 'only ' + daysText(cd).replace(/^every weekday$/, 'on weekdays').replace(/^every weekend$/, 'on weekends').replace(/^every /, 'on ');
    onlyIf = onlyIf ? `${onlyIf} · ${dt}` : dt.charAt(0).toUpperCase() + dt.slice(1);
  }
  const ifEmpty = !onlyIf;
  if (!onlyIf) onlyIf = 'Every time';

  const parts = [];
  const acts = (a.actions || []).map(toMulti).filter((x) => x.entities.length && x.command);
  if (acts.length) parts.push(joinList(acts.map((x) => actionLabel(x, hub)), 'and'));
  if (a.notify.enabled) {
    const labels = a.notify.buttons.map((id) => (choices.find((c) => c.id === id) || { label: id }).label);
    parts.push(labels.length ? `Notify with ${joinList(labels, 'and')} buttons` : 'Send a notification');
  }
  if (a.autoCommand.enabled && a.autoCommand.command) {
    const c = availableCommands(a, hub).find((x) => x.id === a.autoCommand.command);
    parts.push(`${c ? c.label : a.autoCommand.command} automatically`);
  }
  const then = parts.length ? parts.join(' · ') : 'Nothing yet';

  const who = (people || []).filter((p) => a.recipients.includes(p.id)).map((p) => p.name);
  const missing = a.recipients.length - who.length;
  let whoText = (who.length ? joinList(who, 'and') : '') + (missing > 0 && people ? `${who.length ? ' + ' : ''}${missing} removed` : '');
  if (!a.notify.enabled) whoText = 'Nobody — no notification is sent';
  else if (!whoText) whoText = 'Nobody yet';
  else if (a.notify.timeSensitive) whoText += ' · time-sensitive';

  const f = a.followUp;
  let after = f.enabled ? 'Tell everyone who did it' : 'No follow-up';
  if (f.remindMinutes) {
    const times = f.remindTimes || 3;
    const m = f.remindMinutes;
    const every = m % 60 === 0 ? `${m / 60} hour${m === 60 ? '' : 's'}` : `${m} min`;
    after += `${f.enabled ? '; r' : ' · R'}emind again after ${every} if still pending, up to ${times} time${times === 1 ? '' : 's'}`;
  }

  const items = triggerItems(t);
  const whenEmpty = !items.length || items.some((i) => ((i.type === 'state' || i.type === 'property') && !i.entities.length) || (i.type === 'event' && !i.entity));
  const hints = {
    when: !items.length ? 'Choose what starts it' : whenEmpty ? 'Pick a device' : '',
    if: ifEmpty ? 'Optional — leave empty to always run' : '',
    then: parts.length ? '' : 'Choose what should happen: control a device, send a notification, or both',
    who: a.notify.enabled && !who.length ? 'Pick who gets the notification' : '',
    after: '',
  };
  return { when, if: onlyIf, then, who: whoText, after, hints };
}

/** "unlocked", "on", "open"… for a row's state ("active" = each device's own active state). */
export function stateWord(ids, state, hub) {
  const ents = ids.map((i) => hub.entitiesById[i]).filter(Boolean);
  const kind = kindOfIds(ids, hub);
  const k = kind && hub.kindsById[kind];
  if (state === 'active' || !ents.length) {
    const w = k ? activeLabel(k) : null;
    return w ? w.toLowerCase() : 'on';
  }
  return (stateLabelOf(k, state) || String(state)).toLowerCase();
}

/** The state a row's dropdown shows: legacy "active" rows show the kind's own active state. */
export function explicitState(ids, state, hub) {
  if (state !== 'active') return state;
  const kind = kindOfIds(ids, hub);
  const k = kind && hub.kindsById[kind];
  return (k && k.activeState) || 'active';
}

/** Plain words for one "Only if" row (mirrors the hub's wording). */
export function rowText(row, hub) {
  const ids = row.entities.filter((i) => hub.entitiesById[i]);
  if (!ids.length) return '';
  if (isValueRow(row)) return (row.match === 'none' ? 'not: ' : '') + propertyText(row, hub, 'is');
  const n = names(ids, hub);
  const w = stateWord(ids, row.state, hub);
  if (row.match === 'none') return n.length === 1 ? `${n[0]} isn't ${w}` : `none of ${joinList(n, 'or')} is ${w}`;
  if (n.length === 1) return `${n[0]} is ${w}`;
  if (row.match === 'all') return `${joinList(n, 'and')} are all ${w}`;
  return `any of ${joinList(n, 'or')} is ${w}`;
}

/* ---------- Values a device reports (entity.properties from the hub) ---------- */

/** An "Only if" row about a value rather than a state. */
export function isValueRow(r) {
  return !!r && Object.prototype.hasOwnProperty.call(r, 'property');
}

/** How each type of value can be compared (the hub's OPS). "changes" only starts automations. */
export const VALUE_OPS = {
  number: ['above', 'below', 'is', 'changes'],
  text: ['is', 'is_not', 'changes'],
  boolean: ['is', 'changes'],
};

/** Words for a comparison: "goes above" when it starts something, "is above" when it's checked. */
export function opLabel(op, mode) {
  const goes = { above: 'goes above', below: 'goes below', is: 'becomes', is_not: 'is no longer', changes: 'changes' };
  const is = { above: 'is above', below: 'is below', is: 'is', is_not: "isn't", changes: 'changes' };
  return (mode === 'goes' ? goes : is)[op] || op;
}

/** The values the chosen devices all report (by id), from the first device's list. */
export function propsFor(ids, hub) {
  const ents = ids.map((i) => hub.entitiesById[i]).filter(Boolean);
  if (!ents.length) return [];
  const lists = ents.map((e) => e.properties || []);
  return lists[0].filter((p) => lists.every((l) => l.some((x) => x.id === p.id)));
}

export function findProp(ids, propId, hub) {
  return propsFor(ids, hub).find((p) => p.id === propId) || null;
}

/** "54%", "21.5 °C", "yes", "Heating" for a property value. */
export function formatPropValue(p, v) {
  if (v === null || v === undefined || v === '') return '—';
  if (!p) return String(v);
  if (p.type === 'number') {
    const n = Number(v);
    const num = Number.isFinite(n) ? String(Math.round(n * 100) / 100) : String(v);
    return p.unit ? (p.unit === '%' ? `${num}%` : `${num} ${p.unit}`) : num;
  }
  if (p.type === 'boolean') return v === true || /^(true|1|yes|on)$/i.test(String(v)) ? 'yes' : 'no';
  const o = (p.options || []).find((x) => String(x.value) === String(v));
  if (o) return o.label;
  const s = String(v).replace(/_/g, ' ');
  return s.charAt(0).toUpperCase() + s.slice(1);
}

/** Start a comparison for a freshly picked value: a sensible op and its current value. */
export function propertyStart(p, inWhen) {
  if (!p) return { op: '', value: null };
  const ops = (VALUE_OPS[p.type] || VALUE_OPS.text).filter((o) => inWhen || o !== 'changes');
  const op = p.type === 'number' ? 'above' : ops[0];
  let value = p.value === undefined ? null : p.value;
  if (p.type === 'boolean') value = value === true || /^(true|1|yes|on)$/i.test(String(value));
  return { op, value };
}

/** Short label: "Main Floor's humidity goes above 60%". */
function propertyText(spec, hub, mode) {
  const ids = (spec.entities || []).filter((i) => hub.entitiesById[i]);
  if (!ids.length) return '';
  const p = findProp(ids, spec.property, hub);
  const n = names(ids, hub);
  if (!p) return `${joinList(n, 'or')}: pick a value`;
  const label = p.label.charAt(0).toLowerCase() + p.label.slice(1);
  const subj = n.length === 1 ? `${n[0]}'s ${label}` : `the ${label} at ${joinList(n, 'or')}`;
  if (!spec.op) return `${subj} …`;
  if (spec.op === 'changes') return `${subj} changes`;
  return `${subj} ${opLabel(spec.op, mode)} ${formatPropValue(p, spec.value)}`;
}