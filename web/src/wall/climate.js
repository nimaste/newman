// The Climate glance tile's reading of devices - from what a kind can do and what a device reports
// (/api/meta, entity.properties), never from kind ids. Plain functions, checked by `npm test`.
// Numbers are shown exactly as the device reports them (never rounded).

import { categoryScreen } from '../model.js';

export const isTempUnit = (u) => typeof u === 'string' && /^°[CF]$/.test(u.trim());

/** A number command in °C / °F: what you set a thermostat with. */
export function tempCommand(kind) {
  return ((kind && kind.commands) || []).find((c) => c.valueType === 'number' && isTempUnit(c.unit)) || null;
}

/** The "temperature-setting" field filter: something you set to a temperature. */
export const setsTemperature = (kind) => !!tempCommand(kind);

const temps = (e) => ((e && e.properties) || []).filter((p) => p.type === 'number' && isTempUnit(p.unit));

/**
 * The "temperature" field filter: a °C / °F reading that is the device's own state (a temperature
 * sensor), or any temperature a climate device reports (a thermostat, the weather) - not a CPU or a disk.
 */
export function reportsTemperature(e, kind) {
  return temps(e).some((p) => !p.attr || (kind && categoryScreen(kind.category) === 'climate'));
}

function humidityOf(e) {
  const props = (e && e.properties) || [];
  return props.find((p) => p.id === 'humidity' && p.type === 'number') || props.find((p) => p.type === 'number' && p.unit === '%' && /humid/i.test(p.label || '')) || null;
}

const has = (p) => !!p && p.value !== null && p.value !== undefined && p.value !== '';

/** A room box: name, temperature and (if reported) humidity - exactly as reported. */
export function roomOf(e, kind) {
  const t = temps(e);
  const temp = t.find((p) => !p.attr) || t.find((p) => p.id === 'temperature') || (kind && categoryScreen(kind.category) === 'climate' ? t[0] : null) || null;
  const hum = humidityOf(e);
  return { id: e.id, name: e.name, temp: has(temp) ? temp.value : null, unit: temp ? temp.unit : '', humidity: has(hum) ? hum.value : null, off: e.available === false };
}

/** The thermostat: the target (its temperature command's attr), now, humidity, mode and what it's doing. */
export function thermostatOf(e, kind) {
  const cmd = tempCommand(kind);
  if (!e || !cmd) return null;
  const attrs = e.attrs || {};
  const target = cmd.attr && attrs[cmd.attr] !== undefined && attrs[cmd.attr] !== null ? attrs[cmd.attr] : null;
  const now = temps(e).find((p) => p.attr !== cmd.attr && has(p)) || null;
  const hum = humidityOf(e);
  const modeCmd = ((kind && kind.commands) || []).find((c) => c.valueType === 'state');
  const states = (kind && kind.states) || [];
  const mode = modeCmd && e.state ? (states.find((s) => s.id === e.state) || { label: e.state }).label : null;
  const act = ((e.properties || []).find((p) => p.type === 'text' && (p.options || []).length && p.attr && has(p))) || null;
  const action = act ? ((act.options || []).find((o) => String(o.value) === String(act.value)) || { label: String(act.value) }).label : null;
  return {
    min: typeof cmd.min === 'number' ? cmd.min : 10,
    max: typeof cmd.max === 'number' ? cmd.max : 30,
    target,
    now: now ? now.value : null,
    humidity: has(hum) ? hum.value : null,
    mode,
    modeId: modeCmd ? e.state : null,
    action,
    off: e.state === 'off',
  };
}

/** Exactly as reported: 22.9 -> "22.9°", "22.10" stays "22.10°". */
export function deg(v) {
  if (v === null || v === undefined || v === '') return '—';
  return String(v).trim() + '°';
}
export function pct(v) {
  return v === null || v === undefined || v === '' ? '' : String(v).trim() + '%';
}

/** 0 (cool, 16° or under) … 1 (warm, 26° or over) for a room's colour bar (°F converted). */
export function warmth(v, unit = '°C') {
  let t = Number(v);
  if (!Number.isFinite(t)) return null;
  if (/F/.test(unit || '')) t = ((t - 32) * 5) / 9;
  return Math.min(1, Math.max(0, (t - 16) / 10));
}

/** A colour between cool and warm ("#4cb7ff", "#ff8a4c"), at f (0-1). */
export function mix(cool, warm, f) {
  const hex = (h) => [1, 3, 5].map((i) => parseInt(h.slice(i, i + 2), 16));
  const a = hex(cool);
  const b = hex(warm);
  return `rgb(${a.map((v, i) => Math.round(v + (b[i] - v) * f)).join(', ')})`;
}

/** How many room boxes per row: 2 in a narrow tile, more when it's wide (px of tile width). */
export function roomColumns(width, count) {
  const cols = Math.max(2, Math.floor(width / 150));
  return Math.max(1, Math.min(cols, Math.max(count, 1), 6));
}
