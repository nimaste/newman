// The wall's alarms and timers by touch (VoiceLayer.jsx holds the data and the ringing): alarm cards next
// to the timer cards, the ringing screen, and the sheets - Alarms & timers (the big clock, an alarm card,
// "what alarms do I have"), a timer (a timer card or Open), + New timer, and New / Edit alarm. The hub
// keeps everything (api/voice/timers, api/voice/alarms); the sheets only ask it and show what it says.

import { useState } from 'react';
import { api, enc } from '../api.js';
import { Icon } from '../icons.jsx';
import { countdown } from './voiceTimers.js';
import {
  alarmCardName,
  alarmRingLabel,
  alarmSmall,
  alarmTitle,
  DAY_LETTERS,
  dayWord,
  durationWords,
  from24,
  minuteOptions,
  QUICK_TIMERS,
  ringClock,
  timerLeft,
  to24,
} from './alarmTimes.js';

function cap(s) {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

/** The hub's answer, quietly: a failed request just leaves things as they were (the next push puts it right). */
async function quiet(p) {
  try {
    return await p;
  } catch {
    return null;
  }
}

export const timerApi = {
  change: (t, body) => quiet(api.patch('api/voice/timers/' + enc(t.id), body, { quiet: true })),
  cancel: (t) => quiet(api.del('api/voice/timers/' + enc(t.id), { quiet: true })),
  add: (seconds, label) => quiet(api.post('api/voice/timers', { seconds, label }, { quiet: true })),
};

export const alarmApi = {
  add: (body) => quiet(api.post('api/voice/alarms', body, { quiet: true })),
  change: (a, body) => quiet(api.patch('api/voice/alarms/' + enc(a.id), body, { quiet: true })),
  remove: (a) => quiet(api.del('api/voice/alarms/' + enc(a.id), { quiet: true })),
  snooze: (a, minutes) => quiet(api.post('api/voice/alarms/' + enc(a.id) + '/snooze', minutes ? { minutes } : {}, { quiet: true })),
  stop: (a) => quiet(api.post('api/voice/alarms/' + enc(a.id) + '/stop', {}, { quiet: true })),
};

/* ---------------- cards ---------------- */

/** The next alarm (or a snoozed one, counting down to its next ring) beside the timer cards. */
export function AlarmCard({ a, now, onOpen, onStop }) {
  const snoozed = a.state === 'snoozed';
  return (
    <div className="vo-timer vo-alarm" role="status" aria-label={alarmCardName(a)}>
      <button type="button" className="vo-timer-main is-open" onClick={onOpen}>
        <span className="vo-timer-name">{alarmCardName(a)}</span>
        <span className="vo-timer-time">{snoozed ? countdown(a.snoozeUntil - now) : a.time}</span>
        {!snoozed && a.nextAt ? <span className="vo-timer-where">{dayWord(new Date(a.nextAt * 1000))}</span> : null}
      </button>
      {snoozed ? (
        <button type="button" className="vo-timer-x" onClick={onStop} aria-label="Stop the snooze">
          <Icon name="x" size={18} strokeWidth={2.4} />
        </button>
      ) : null}
    </div>
  );
}

/* ---------------- the ringing screen ---------------- */

/** Full screen while an alarm or a timer rings: Snooze N min / +1 min, and Stop. */
export function RingScreen({ ring, snoozeMinutes, phrase, onSnooze, onMore, onStop }) {
  const alarm = ring.kind === 'alarm';
  const it = ring.item;
  return (
    <div className="vo-ring" role="alertdialog" aria-label={alarm ? alarmRingLabel(it) : it.name}>
      <span className="vo-ring-label">{alarm ? alarmRingLabel(it) : it.name.toUpperCase()}</span>
      <span className="vo-ring-big">{alarm ? ringClock(it.hour, it.minute) : 'Done'}</span>
      {alarm ? (it.repeat ? <span className="vo-ring-src">{it.repeat}</span> : null) : <span className="vo-ring-src">{durationWords(it.seconds)}</span>}
      <div className="vo-ring-btns">
        {alarm ? (
          <button type="button" className="vo-ring-b" onClick={onSnooze}>
            Snooze {snoozeMinutes} min
          </button>
        ) : (
          <button type="button" className="vo-ring-b" onClick={onMore}>
            +1 min
          </button>
        )}
        <button type="button" className="vo-ring-b is-stop" onClick={onStop}>
          Stop
        </button>
      </div>
      <span className="vo-ring-say">
        or say “{phrase}, stop” / “{alarm ? 'snooze' : 'another minute'}”
      </span>
    </div>
  );
}

/* ---------------- sheets ---------------- */

function Sheet({ title, onClose, children, className = '' }) {
  return (
    <div className="vo-sheet-dim" onPointerDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className={'vo-sheet ' + className} role="dialog" aria-label={title}>
        <div className="vo-sheet-head">
          <span>{title}</span>
          <button type="button" className="vo-sheet-x" onClick={onClose} aria-label="Close">
            <Icon name="x" size={22} strokeWidth={2.4} />
          </button>
        </div>
        {children}
      </div>
    </div>
  );
}

function Toggle({ on, onChange, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} className={'vo-tg' + (on ? ' is-on' : '')} onClick={() => onChange(!on)} />
  );
}

/** Alarms & timers: the timers first (time left, of its full time, ⏸ / ▶, Open), then the alarms (on / off, Edit). */
export function AlarmsSheet({ timers, alarms, now, onClose, onOpenTimer, onNewTimer, onEditAlarm }) {
  return (
    <Sheet title="Alarms & timers" onClose={onClose}>
      <div className="vo-sheet-scroll">
        <div className="vo-sec">Timers</div>
        {timers.length ? (
          timers.map((t) => (
            <div className="vo-row" key={t.id}>
              <span className="vo-row-tm">{t.state === 'ringing' ? 'Done' : countdown(timerLeft(t, now))}</span>
              <span className="vo-row-d">
                {cap(t.name)}
                <small>{t.state === 'paused' ? `Paused · of ${countdown(t.seconds)}` : `of ${countdown(t.seconds)}`}</small>
              </span>
              {t.state === 'running' || t.state === 'paused' ? (
                <button type="button" className="vo-pill" aria-label={t.state === 'paused' ? `Resume the ${t.name}` : `Pause the ${t.name}`}
                  onClick={() => timerApi.change(t, { do: t.state === 'paused' ? 'resume' : 'pause' })}>
                  <Icon name={t.state === 'paused' ? 'play' : 'pause'} size={18} strokeWidth={2.4} />
                </button>
              ) : (
                <span />
              )}
              <button type="button" className="vo-pill" onClick={() => onOpenTimer(t)}>
                Open
              </button>
            </div>
          ))
        ) : (
          <div className="vo-empty">No timers</div>
        )}
        <div className="vo-row2">
          <button type="button" className="vo-pill is-pri" onClick={onNewTimer}>
            + New timer
          </button>
        </div>
        <div className="vo-sec">Alarms</div>
        {alarms.length ? (
          alarms.map((a) => {
            const on = a.enabled || a.state !== 'set';
            return (
              <div className="vo-row" key={a.id}>
                <span className={'vo-row-tm' + (on ? '' : ' is-off')}>{a.time}</span>
                <span className="vo-row-d">
                  {alarmTitle(a)}
                  <small>{alarmSmall(a)}</small>
                </span>
                <Toggle on={on} label={alarmTitle(a)} onChange={(v) => (v ? alarmApi.change(a, { enabled: true }) : a.state === 'set' ? alarmApi.change(a, { enabled: false }) : alarmApi.stop(a).then(() => alarmApi.change(a, { enabled: false })))} />
                <button type="button" className="vo-pill" onClick={() => onEditAlarm(a)}>
                  Edit
                </button>
              </div>
            );
          })
        ) : (
          <div className="vo-empty">No alarms</div>
        )}
        <div className="vo-row2">
          <button type="button" className="vo-pill is-pri" onClick={() => onEditAlarm(null)}>
            + New alarm
          </button>
        </div>
      </div>
    </Sheet>
  );
}

/** One timer: big countdown, of its full time; −1 / +1 / +5 min; Cancel, Restart, Pause / Resume. */
export function TimerSheet({ t, now, onClose }) {
  const paused = t.state === 'paused';
  return (
    <Sheet title={cap(t.name)} onClose={onClose} className="is-center">
      <span className="vo-big">{t.state === 'ringing' ? 'Done' : countdown(timerLeft(t, now))}</span>
      <span className="vo-of">of {countdown(t.seconds)}</span>
      <div className="vo-row2 is-center">
        <button type="button" className="vo-pill" onClick={() => timerApi.change(t, { do: 'take', seconds: 60 })}>
          −1 min
        </button>
        <button type="button" className="vo-pill" onClick={() => timerApi.change(t, { do: 'add', seconds: 60 })}>
          +1 min
        </button>
        <button type="button" className="vo-pill" onClick={() => timerApi.change(t, { do: 'add', seconds: 300 })}>
          +5 min
        </button>
      </div>
      <div className="vo-row2 is-center">
        <button type="button" className="vo-pill is-del" onClick={() => (timerApi.cancel(t), onClose())}>
          Cancel
        </button>
        <button type="button" className="vo-pill" onClick={() => timerApi.change(t, { do: 'restart' })}>
          Restart
        </button>
        {t.state === 'running' || paused ? (
          <button type="button" className="vo-pill is-pri" onClick={() => timerApi.change(t, { do: paused ? 'resume' : 'pause' })}>
            {paused ? 'Resume' : 'Pause'}
          </button>
        ) : null}
      </div>
    </Sheet>
  );
}

const HOURS = Array.from({ length: 24 }, (_, i) => i);
const MINUTES = Array.from({ length: 60 }, (_, i) => i);

/** + New timer: a quick pick starts one at once; or hours and minutes and a name, then Start. */
export function NewTimerSheet({ onClose }) {
  const [h, setH] = useState(0);
  const [m, setM] = useState(0);
  const [name, setName] = useState('');
  const start = async (seconds, label) => {
    await timerApi.add(seconds, label);
    onClose();
  };
  const secs = h * 3600 + m * 60;
  return (
    <Sheet title="New timer" onClose={onClose}>
      <div className="vo-row2">
        {QUICK_TIMERS.map(([s, words]) => (
          <button type="button" className="vo-pill" key={s} onClick={() => start(s, name.trim())}>
            {words}
          </button>
        ))}
      </div>
      <div className="vo-row2">
        <span className="vo-lab">Or</span>
        <select className="vo-sel" value={h} onChange={(e) => setH(Number(e.target.value))} aria-label="Hours">
          {HOURS.map((x) => (
            <option key={x} value={x}>
              {x} h
            </option>
          ))}
        </select>
        <select className="vo-sel" value={m} onChange={(e) => setM(Number(e.target.value))} aria-label="Minutes">
          {MINUTES.map((x) => (
            <option key={x} value={x}>
              {x} min
            </option>
          ))}
        </select>
      </div>
      <div className="vo-row2">
        <span className="vo-lab">Name</span>
        <input className="vo-input" value={name} maxLength={40} onChange={(e) => setName(e.target.value)} aria-label="Name" />
      </div>
      <div className="vo-row2">
        <span className="vo-flex" />
        <button type="button" className="vo-pill" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="vo-pill is-pri" disabled={!secs} onClick={() => start(secs, name.trim())}>
          Start
        </button>
      </div>
    </Sheet>
  );
}

/** New alarm / Edit alarm: time (hour, minute, AM / PM), the days it repeats, a name; Delete, Cancel, Save. */
export function AlarmEditSheet({ a, onClose }) {
  const start = a ? from24(a.hour) : { h12: 7, ampm: 'AM' };
  const [h12, setH12] = useState(start.h12);
  const [minute, setMinute] = useState(a ? a.minute : 0);
  const [ampm, setAmpm] = useState(start.ampm);
  const [days, setDays] = useState(new Set(a ? a.days : []));
  const [label, setLabel] = useState(a ? a.label : '');
  const flip = (d) =>
    setDays((s) => {
      const n = new Set(s);
      if (n.has(d)) n.delete(d);
      else n.add(d);
      return n;
    });
  const save = async () => {
    const body = { hour: to24(h12, ampm), minute, days: [...days].sort(), label: label.trim() };
    if (a) await alarmApi.change(a, { ...body, enabled: true });
    else await alarmApi.add(body);
    onClose();
  };
  return (
    <Sheet title={a ? 'Edit alarm' : 'New alarm'} onClose={onClose}>
      <div className="vo-row2">
        <span className="vo-lab">Time</span>
        <select className="vo-sel" value={h12} onChange={(e) => setH12(Number(e.target.value))} aria-label="Hour">
          {Array.from({ length: 12 }, (_, i) => i + 1).map((x) => (
            <option key={x} value={x}>
              {x}
            </option>
          ))}
        </select>
        <span className="vo-colon">:</span>
        <select className="vo-sel" value={minute} onChange={(e) => setMinute(Number(e.target.value))} aria-label="Minute">
          {minuteOptions(a ? a.minute : null).map((x) => (
            <option key={x} value={x}>
              {String(x).padStart(2, '0')}
            </option>
          ))}
        </select>
        <select className="vo-sel" value={ampm} onChange={(e) => setAmpm(e.target.value)} aria-label="AM or PM">
          <option value="AM">AM</option>
          <option value="PM">PM</option>
        </select>
      </div>
      <div className="vo-row2">
        <span className="vo-lab">Repeat</span>
        <span className="vo-days">
          {DAY_LETTERS.map((l, d) => (
            <button type="button" key={d} className={days.has(d) ? 'is-on' : ''} aria-pressed={days.has(d)} onClick={() => flip(d)}>
              {l}
            </button>
          ))}
        </span>
      </div>
      <div className="vo-row2">
        <span className="vo-lab">Name</span>
        <input className="vo-input" value={label} maxLength={40} onChange={(e) => setLabel(e.target.value)} aria-label="Name" />
      </div>
      <div className="vo-row2">
        {a ? (
          <button type="button" className="vo-pill is-del" onClick={() => (alarmApi.remove(a), onClose())}>
            Delete
          </button>
        ) : null}
        <span className="vo-flex" />
        <button type="button" className="vo-pill" onClick={onClose}>
          Cancel
        </button>
        <button type="button" className="vo-pill is-pri" onClick={save}>
          Save
        </button>
      </div>
    </Sheet>
  );
}

/* ---------------- when the wall can't listen ---------------- */

/** The microphone isn't allowed (with the way to allow it), or speech isn't available on the hub (a plain line). */
export function VoiceProblem({ problem, onAllow }) {
  if (!problem) return null;
  if (problem.kind === 'mic') {
    return (
      <div className="vo-banner" role="status">
        <span className="vo-banner-ic" aria-hidden="true">
          🎙
        </span>
        <span>
          <b>{problem.phrase} can't hear you.</b> Newman Wall isn't allowed to use the microphone.
        </span>
        <button type="button" className="vo-pill" onClick={onAllow}>
          Allow the microphone
        </button>
      </div>
    );
  }
  return (
    <div className="vo-banner is-plain" role="status">
      {problem.phrase} isn't available: {problem.reason}.
    </div>
  );
}
