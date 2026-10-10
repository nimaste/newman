// Helpers that interpret hub data generically via /api/meta (no per-kind code).

export function isActive(entity, kind) {
  return !!(kind && kind.activeState && entity && entity.state === kind.activeState);
}

export function stateLabelOf(kind, stateId) {
  const s = kind && (kind.states || []).find((x) => x.id === stateId);
  return s ? s.label : stateId;
}

/**
 * A device's label as the hub words it (entity.stateLabel: a kind's note such as a wall tablet's
 * "Screen off" wins over its state), else its state's label from meta, else the raw state.
 */
export function entityLabel(entity, kind) {
  if (!entity) return '';
  return entity.stateLabel || stateLabelOf(kind, entity.state) || entity.state || '';
}

export function activeLabel(kind) {
  return kind && kind.activeState ? stateLabelOf(kind, kind.activeState) : '';
}

/**
 * CSS class for a state value. Security kinds: amber when "active"
 * (unlocked/open), teal when calm. Other kinds (lights…): teal when active
 * (on), muted otherwise. Grey when unavailable.
 */
export function stateClass(entity, kind) {
  if (!entity || entity.available === false || entity.state === 'offline') return 'state-off';
  if (entity.attrs && entity.attrs.counts && countList(entity.attrs.counts).length) return 'state-active';
  if (!kind || !kind.activeState) return '';
  if (kind.secureState) return notSecure(entity, kind) ? 'state-active' : 'state-calm';
  return isActive(entity, kind) ? 'state-calm' : 'state-off';
}

/**
 * Worth a look: the kind has a secure state (kind.secureState from /api/meta) and the
 * device has settled somewhere else (unlocked, open). Momentary states (locking…) don't count.
 */
export function notSecure(entity, kind) {
  return !!(kind && kind.secureState && entity && entity.available !== false && entity.state && entity.state !== kind.secureState && !isTransitional(entity, kind));
}

/** The house-mode device (an alarm): kind.isMode from /api/meta. */
export function isModeKind(kind) {
  return !!(kind && kind.isMode);
}

/**
 * Events a device can actually emit: the kind's events, narrowed to
 * entity.attrs.events when present (a Pico remote only has some buttons).
 */
export function eventsFor(entity, kind) {
  const all = (kind && kind.events) || [];
  const own = entity && entity.attrs && Array.isArray(entity.attrs.events) ? entity.attrs.events : null;
  if (!own) return all;
  // In the kind's order; anything the device reports that its kind doesn't list goes last.
  const unknown = own.filter((id) => !all.some((e) => e.id === id)).map((id) => ({ id, label: id.charAt(0).toUpperCase() + id.slice(1).replace(/_/g, ' ') }));
  return [...all.filter((e) => own.includes(e.id)), ...unknown];
}

/** A button-only device (Pico remote): lists its events, takes no commands, has no real state. */
export function isRemote(entity, kind) {
  return !!(
    entity &&
    entity.attrs &&
    Array.isArray(entity.attrs.events) &&
    !((kind && kind.commands) || []).length &&
    ((kind && kind.states) || []).length <= 1
  );
}

const PLURAL = { person: 'people', bus: 'buses', license_plate: 'license plates', face: 'faces', package: 'packages' };
export function countLabel(label, n) {
  const name = label.replace(/_/g, ' ');
  return `${n} ${n === 1 ? name : PLURAL[label] || name + 's'}`;
}

/** Non-zero detection counts as ['1 person', '2 cars']. */
export function countList(counts) {
  return Object.entries(counts || {})
    .filter(([, n]) => Number(n) > 0)
    .map(([k, n]) => countLabel(k, Number(n)));
}

/** "On pressed" -> "On", "Person detected" -> "Person", "Car entered" -> "Car". */
export function shortEventLabel(label) {
  const s = String(label || '');
  const quoted = /^[“"](.+)[”"]\s+is pressed$/i.exec(s);
  if (quoted) return quoted[1];
  return s.replace(/\s+(pressed|detected|entered|comes in)$/i, '') || label;
}

/** Button names of a remote, e.g. ['On', 'Favorite', 'Off', 'Raise', 'Lower']. */
export function buttonNames(entity, kind) {
  return eventsFor(entity, kind).map((e) => shortEventLabel(e.label));
}

function fmtTemp(v) {
  return typeof v === 'number' ? (Math.round(v * 10) / 10).toFixed(1) : null;
}

/**
 * Every state in plain words:
 *   lock "Locked" · light "On · 60%" / "Off" · garage "Opening…" · doorbell "Pressed just now"
 *   camera "Clear" / "1 person · 1 car" · zone "Occupied · 2 people" · thermostat "Cool · 22.7 °C → 22.5"
 *   remote "5 buttons (On, Off, …)" (long: "Buttons: On, Favorite, Off, Raise, Lower")
 */
export function entityStateText(entity, kind, { long = false } = {}) {
  if (!entity) return '';
  if (entity.available === false || entity.state === 'offline') return 'Offline';
  const a = entity.attrs || {};
  const label = entity.stateLabel || entity.state || '—';

  if (isRemote(entity, kind)) {
    // Event-only devices: "buttons" when every event is a press (by its label), else "events".
    const names = buttonNames(entity, kind);
    const word = eventsFor(entity, kind).every((ev) => /pressed/i.test(ev.label || '')) ? 'button' : 'event';
    if (!names.length) return `No ${word}s`;
    if (long || names.length <= 2) return `${word.charAt(0).toUpperCase() + word.slice(1)}s: ` + names.join(', ');
    return `${names.length} ${word}s (${names.slice(0, 2).join(', ')}, …)`;
  }
  if (a.counts && typeof a.counts === 'object') {
    const counts = countList(a.counts);
    // Kinds with an active state ("Occupied") lead with it; others just list what is seen.
    const act = activeLabel(kind);
    if (!counts.length) return act ? label : 'Clear';
    return (act ? [act, ...counts] : counts).join(' · ');
  }
  if (typeof a.currentTemp === 'number') {
    const unit = a.unit || (kind && kind.unit) || '°C';
    const now = `${fmtTemp(a.currentTemp)} ${unit}`;
    const target = fmtTemp(a.targetTemp);
    return entity.state === 'off' || !target ? `${label} · ${now}` : `${label} · ${now} → ${target}`;
  }
  // A plain reading (a kind without states, the state is the number): "Humidity · 64 %".
  const reading = readingText(entity, kind);
  if (reading) return reading;
  // Something you only press (a button): no state to show.
  if (kind && !(kind.states || []).length && (kind.commands || []).length && !entity.state) return '';
  if (isTransitional(entity, kind)) return label.replace(/…$/, '') + '…';
  // A state that is also one of its kind's events (a doorbell's "pressed") only lasts a moment.
  if (kind && (kind.events || []).some((ev) => ev.id === entity.state)) return `${label} just now`;
  // Something playing (a speaker): what's on.
  if (a.track) return `${label} · ${a.track}${a.artist ? ' — ' + a.artist : ''}`;
  if (a.title && entity.state !== 'idle') return `${label} · ${a.title}${a.artist ? ' — ' + a.artist : ''}`;
  const b = a.brightness;
  const on = kind && kind.activeState ? entity.state === kind.activeState : entity.state !== 'off';
  if (typeof b === 'number' && on && a.dimmable !== false) return `${label} · ${Math.round(b)}%`;
  return label;
}

/**
 * For kinds without states whose state is a number: the value with its unit (attrs.unitOfMeasurement,
 * else the state property's unit, else the kind's), led by attrs.deviceClass ("Humidity · 64 %").
 */
export function readingText(entity, kind, { withLabel = true } = {}) {
  if (!entity || !kind || (kind.states || []).length) return '';
  const raw = entity.state;
  const v = typeof raw === 'number' ? raw : raw !== null && raw !== '' && !Number.isNaN(Number(raw)) ? Number(raw) : null;
  if (v === null) return '';
  const a = entity.attrs || {};
  const prop = (entity.properties || []).find((p) => !p.attr);
  const unit = a.unitOfMeasurement || (prop && prop.unit) || kind.unit || '';
  const num = Math.abs(v) >= 100 ? Math.round(v) : Math.round(v * 10) / 10;
  const text = `${num.toLocaleString()}${unit ? ' ' + unit : ''}`;
  const cls = a.deviceClass ? String(a.deviceClass).replace(/_/g, ' ') : '';
  return withLabel && cls ? `${cls.charAt(0).toUpperCase() + cls.slice(1)} · ${text}` : text;
}

/** Garage doors etc. report opening/closing: meta kind.transitional lists those states. */
export function isTransitional(entity, kind) {
  return !!(kind && Array.isArray(kind.transitional) && entity && kind.transitional.includes(entity.state));
}

/** [{area, items}] sorted by room, devices without a room last (area: 'No room'). */
export function groupByArea(list) {
  if (!list.some((e) => e.attrs && e.attrs.area)) return [{ area: null, items: list }];
  const key = (e) => (e.attrs && e.attrs.area) || '';
  const areas = [...new Set(list.map(key))].sort((a, b) => (!a ? 1 : !b ? -1 : a.localeCompare(b)));
  return areas.map((area) => ({ area: area || 'No room', items: list.filter((e) => key(e) === area) }));
}

/** Commands usable as one-tap actions (no value needed). */
export function plainCommands(kind) {
  return ((kind && kind.commands) || []).filter((c) => !c.takesValue);
}

/**
 * A start/stop duo among several buttons (a vacuum's Start / Pause next to Send home, Find): one
 * command confirming to the kind's active state, one to another state. Only the one that makes
 * sense now is shown. Found from meta, not by id; devices with just the two keep both.
 */
export function toggleDuo(kind, plain) {
  if (!kind || !kind.activeState || plain.length <= 2) return null;
  // Things you position (a blind: Open / Close / Stop + a position slider) keep every button.
  if ((kind.commands || []).some((c) => c.valueType === 'percent')) return null;
  const on = plain.find((c) => c.confirmsTo === kind.activeState);
  const off = plain.find((c) => c !== on && c.confirmsTo && c.confirmsTo !== kind.activeState);
  return on && off ? { on, off } : null;
}

/** The states a "state" command can set: the kind's states without the momentary ones. */
export function stateChoices(kind) {
  const tr = (kind && kind.transitional) || [];
  return ((kind && kind.states) || []).filter((s) => !tr.includes(s.id));
}

/**
 * A value command's current value on a device: attrs[cmd.attr] (the hub names the attribute);
 * a "state" command without an attr uses the device's state.
 */
export function commandCurrent(entity, cmd) {
  if (!entity || !cmd) return null;
  const a = entity.attrs || {};
  if (cmd.attr) return a[cmd.attr] ?? null;
  if (cmd.valueType === 'state') return entity.state ?? null;
  return null;
}

/**
 * What a "choices" command can pick from: the device's attrs[cmd.attr] list of {id, name}
 * (a vacuum's rooms…). Empty when the device hasn't said yet.
 */
export function choiceItems(entity, cmd) {
  const list = entity && cmd && cmd.attr && entity.attrs ? entity.attrs[cmd.attr] : null;
  return Array.isArray(list) ? list.filter((x) => x && x.id !== undefined && x.id !== null).map((x) => ({ id: x.id, name: x.name || String(x.id) })) : [];
}

/** Commands whose value is a pick of the device's items (valueType "choices"). */
export function choiceCommands(kind) {
  return ((kind && kind.commands) || []).filter((c) => c.valueType === 'choices');
}

/** "80%", "21.5 °C", "Heat", "Kitchen and Hall" for a command value (`entity` names chosen items). */
export function valueText(cmd, kind, v, entity) {
  if (cmd && cmd.valueType === 'choices') {
    const ids = Array.isArray(v) ? v : v === null || v === undefined || v === '' ? [] : [v];
    if (!ids.length) return 'nothing chosen';
    const items = choiceItems(entity, cmd);
    const names = ids.map((id) => (items.find((x) => String(x.id) === String(id)) || {}).name || String(id));
    return names.length <= 3 ? names.join(names.length === 2 ? ' and ' : ', ') : `${names.slice(0, 2).join(', ')} and ${names.length - 2} more`;
  }
  if (v === null || v === undefined || v === '') return '?';
  if (cmd && cmd.valueType === 'state') return stateLabelOf(kind, v);
  const u = (cmd && cmd.unit) || '';
  if (!u) return String(v);
  return u === '%' ? `${v}%` : `${v} ${u}`;
}

export function issuesOf(entity) {
  return (entity && entity.health && entity.health.issues) || [];
}

/**
 * Kind icon. A kind-id map is only a nicety; an unknown kind falls back to
 * its capabilities (camera, first command's icon, events), then its category, then a generic one.
 */
export function kindIcon(kind) {
  if (!kind) return 'dot';
  const byKind = { lock: 'lock', doorbell: 'bell', contact: 'door', motion: 'motion', alarm: 'shield', camera: 'camera', light: 'light', remote: 'grid', zone: 'camera', thermostat: 'thermometer', temperature: 'thermometer', garage: 'garage', weather: 'cloud', calendar: 'calendar', vacuum: 'vacuum', switch: 'power', fan: 'fan', cover: 'blinds', sensor: 'gauge', leak: 'droplet', smoke: 'flame', binary: 'toggle', siren: 'megaphone', button: 'pointer', speaker: 'speaker', music_player: 'music', recipe_book: 'recipe', webhook: 'webhook', upkeep: 'wrench' };
  const byCat = { security: 'shield', garage: 'garage', lighting: 'light', climate: 'thermometer', cleaning: 'vacuum', cameras: 'camera' };
  if (byKind[kind.id]) return byKind[kind.id];
  if (kind.hasCamera) return 'camera';
  const cmd = (kind.commands || []).find((c) => c.icon);
  if (cmd) return cmd.icon;
  if ((kind.events || []).length) return 'bell';
  if (kind.unit) return 'thermometer';
  return byCat[kind.category] || 'dot';
}

/** Strip server-computed fields before PUT/POST/describe. */
export function automationBody(a, { keepId = true } = {}) {
  // eslint-disable-next-line no-unused-vars
  const { summary, lastRun, nextRun, id, ...rest } = a;
  return keepId && id ? { id, ...rest } : rest;
}

/** lastRun/nextRun may be a timestamp or a Run-like object. */
export function runTime(r) {
  if (r === null || r === undefined) return null;
  if (typeof r === 'object') return r.startedAt || r.at || r.ts || null;
  return r;
}
