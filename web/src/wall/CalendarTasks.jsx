// Tasks on the wall's calendar (approved WallCalendar / WallNewTask / WallTaskCard boards, and the Echo
// Show 5 ones): GCloud Calendar's to-dos, read and written through the hub (GET api/tasks; POST
// api/tasks, api/tasks/change, api/tasks/delete, api/tasks/done). Tasks show on their day with a tick box
// in the calendar's colour (overdue in coral; done ticked, faded and struck through); tapping one opens
// its card - Done (asks who when the task says so), Edit, Delete, Close, and Done before. New task and
// Edit share one form. The wording of the choices is GCloud's own task form's, word for word, so a task
// reads the same everywhere.

import { useCallback, useEffect, useState } from 'react';
import { api, toast } from '../api.js';
import { useLive } from '../live.js';
import { CustomRepeat, DatePicker, TimePicker } from './CalendarEvents.jsx';
import { PEOPLE_COLOURS } from './today.js';
import { customOf, customRule, dateKey, shortDate } from './rrule.js';

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad = (n) => String(n).padStart(2, '0');
const CORAL = '#ff8a6b';

const parseDay = (k) => {
  const [y, m, d] = String(k || '').slice(0, 10).split('-').map(Number);
  return y && m && d ? new Date(y, m - 1, d) : null;
};
const atMidnight = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
const daysFrom = (a, b) => Math.round((atMidnight(b) - atMidnight(a)) / 864e5);
function clock(h, m) {
  return `${h % 12 || 12}:${pad(m)} ${h < 12 ? 'AM' : 'PM'}`;
}
const timeOf = (t) => (t.dueAt ? clock(Number(t.dueAt.slice(11, 13)), Number(t.dueAt.slice(14, 16))) : '');
const monthDay = (d) => `${MONTHS[d.getMonth()]} ${d.getDate()}`;

/** GCloud's repeat words: "every 3 months", "every week" ("" when it doesn't repeat). */
export function everyWords(rule) {
  const p = {};
  String(rule || '')
    .split(';')
    .forEach((x) => {
      const [k, v] = x.split('=');
      if (k && v) p[k.toUpperCase()] = v;
    });
  const unit = { DAILY: ['day', 'days'], WEEKLY: ['week', 'weeks'], MONTHLY: ['month', 'months'], YEARLY: ['year', 'years'] }[p.FREQ];
  if (!unit) return '';
  const n = Number(p.INTERVAL) || 1;
  return n > 1 ? `every ${n} ${unit[1]}` : `every ${unit[0]}`;
}

/** "Every 3 months, from when it’s done", "Doesn’t repeat" (the card). */
export function repeatsText(t) {
  const w = everyWords(t.rrule);
  if (!w) return 'Doesn’t repeat';
  return w.replace(/^every/, 'Every') + (t.repeatFrom === 'done' ? ', from when it’s done' : '');
}

/** A task's state for the calendar: 'done' | 'late' | 'open'. */
export function taskState(t, today) {
  if (t.done) return 'done';
  const d = parseDay(t.due);
  return d && d < today ? 'late' : 'open';
}

/** The tasks (and done marks) on one day: open and done tasks on their due day, and each time a
 * repeating task was done, on the day it was done. */
export function tasksOn(tasks, day) {
  const k = dateKey(day);
  const out = [];
  (tasks || []).forEach((t) => {
    if (t.due === k) out.push({ key: t.id, task: t, done: !!t.done });
    if (t.rrule)
      (t.history || []).forEach((h, i) => {
        if (dateKey(new Date(h.at)) === k) out.push({ key: `${t.id}#${i}`, task: t, done: true, at: h.at, who: h.who });
      });
  });
  return out;
}

/** "Due Fri, Oct 30 · in 3 days", "Due today at 9:30 AM", "Was due Sat, Oct 24 · 3 days late", "Done Oct 9". */
export function dueLine(t, today) {
  const d = parseDay(t.due);
  if (t.done) return t.completed ? `Done ${monthDay(new Date(t.completed))}` : 'Done';
  if (!d) return 'No due day';
  const n = daysFrom(today, d);
  const at = timeOf(t) ? ` at ${timeOf(t)}` : '';
  if (n < 0) return `Was due ${shortDate(d)}${at} · ${-n === 1 ? '1 day late' : `${-n} days late`}`;
  if (n === 0) return `Due today${at}`;
  return `Due ${shortDate(d)}${at} · ${n === 1 ? 'tomorrow' : `in ${n} days`}`;
}

/** {tasks, calendars, people, reload}: GET api/tasks, again when a task changes (the hub's "today"). */
export function useTasks() {
  const [data, setData] = useState({ tasks: [], calendars: [], people: [] });
  const reload = useCallback(() => {
    api
      .get('api/tasks', { quiet: true })
      .then((d) => setData({ tasks: d.tasks || [], calendars: d.calendars || [], people: d.people || [] }))
      .catch(() => {});
  }, []);
  useEffect(() => {
    reload();
    const t = setInterval(reload, 60000);
    return () => clearInterval(t);
  }, [reload]);
  useLive((m) => {
    if (m.type === 'today' || m.type === 'reconnected') reload();
  });
  return { ...data, reload };
}

/** The tick box: the calendar's colour (coral when late); done: filled and ticked. */
export function TaskBox({ task, state, size = 'line' }) {
  const colour = state === 'late' ? CORAL : task.colour || '#3CCFB4';
  return (
    <span className={'tk-box tk-box--' + size + (state === 'done' ? ' is-done' : '')} style={{ borderColor: colour, background: state === 'done' ? colour : 'transparent' }} aria-hidden="true">
      {state === 'done' ? '✓' : ''}
    </span>
  );
}

/** Done, asking who first when the task says so: the household as circles, and Skip. */
function WhoChooser({ people, onPick, onCancel }) {
  return (
    <div className="tk-who">
      <span className="tk-who-q">Who did it?</span>
      <div className="tk-who-grid">
        {people.map((p, i) => (
          <button key={p.id} type="button" className="tk-person" aria-label={`${p.name} did it`} onClick={() => onPick(p.name)}>
            <span className="tk-circle" style={{ background: PEOPLE_COLOURS[i % PEOPLE_COLOURS.length] }}>
              {p.name.charAt(0).toUpperCase()}
            </span>
            <span className="tk-pname">{p.name}</span>
          </button>
        ))}
      </div>
      <div className="tk-who-btns">
        <button type="button" className="ce-btn" onClick={() => onPick(null)}>
          Skip · just mark it done
        </button>
        <button type="button" className="ce-btn" onClick={onCancel}>
          Cancel
        </button>
      </div>
    </div>
  );
}

/** Done (with who); a toast says when it's due next. */
export async function markTaskDone(task, who) {
  try {
    const r = await api.post('api/tasks/done', who ? { id: task.id, who } : { id: task.id }, { quiet: true });
    const next = r && r.nextDue ? parseDay(r.nextDue) : null;
    toast(next ? `Done · next due ${shortDate(next)}` : 'Done', 'success');
    return true;
  } catch (e) {
    toast((e && e.message) || 'That didn’t work', 'error');
    return false;
  }
}

/* ---------------- the card ---------------- */

export function TaskCard({ task: t, people, today, short, asking: askFirst, onClose, onEdit, onChanged }) {
  const [asking, setAsking] = useState(!!askFirst);
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const state = taskState(t, today);
  const last = (t.history || [])[0];
  const done = async (who) => {
    setBusy(true);
    const ok = await markTaskDone(t, who);
    setBusy(false);
    if (ok) {
      onChanged();
      onClose();
    }
  };
  const del = async () => {
    setBusy(true);
    try {
      const r = await api.post('api/tasks/delete', { id: t.id });
      toast(`Deleted from ${r.calendar}`, 'success');
      onChanged();
      onClose();
    } catch (e) {
      setBusy(false);
      toast((e && e.message) || 'Couldn’t delete it', 'error');
    }
  };
  const histDay = (at) => {
    const d = new Date(at);
    return short ? monthDay(d) : `${monthDay(d)}, ${d.getFullYear()}`;
  };
  return (
    <div className={'wt-overlay ce-shade' + (short ? ' tk-full' : '')} role="dialog" aria-modal="true" aria-label={t.title} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="ce-card tk-card">
        <div className="ce-card-main">
          <div className="ce-card-head">
            <TaskBox task={t} state={state} size="card" />
            <span className="ce-card-heading">
              <span className={'ce-card-title' + (state === 'done' ? ' tk-struck' : '')}>{t.title}</span>
              {short ? (
                <span className="ce-card-when">
                  {dueLine(t, today)} · <span style={{ color: t.colour }}>{t.calendar}</span>
                </span>
              ) : (
                <>
                  <span className="ce-card-when" style={state === 'late' ? { color: CORAL } : null}>
                    {dueLine(t, today)}
                  </span>
                  <span className="ce-card-cal" style={{ color: t.colour }}>
                    {t.calendar}
                  </span>
                </>
              )}
            </span>
          </div>
          {asking ? (
            <WhoChooser people={people} onPick={(who) => !busy && done(who)} onCancel={() => setAsking(false)} />
          ) : (
            <>
              <div className="tk-facts">
                <div className="ce-field">
                  <span className="ce-label">BELONGS TO</span>
                  <span className="ce-value">{t.owner || 'Everyone'}</span>
                </div>
                <div className="ce-field">
                  <span className="ce-label">REPEATS</span>
                  <span className="ce-value">{repeatsText(t)}</span>
                </div>
                <div className="ce-field">
                  <span className="ce-label">LAST DONE</span>
                  <span className="ce-value">{last ? `${monthDay(new Date(last.at))} · ${last.who}` : 'Not yet'}</span>
                </div>
                {t.notes ? (
                  <div className="ce-field">
                    <span className="ce-label">NOTES</span>
                    <span className="ce-value ce-notes">{t.notes}</span>
                  </div>
                ) : null}
              </div>
              <div className="ce-card-btns">
                {t.writable && !t.done ? (
                  <button type="button" className="ce-btn is-primary tk-done" disabled={busy} onClick={() => (t.askWho && people.length ? setAsking(true) : done(null))}>
                    Done
                  </button>
                ) : null}
                {t.writable ? (
                  <button type="button" className="ce-btn" onClick={onEdit}>
                    Edit
                  </button>
                ) : null}
                {t.writable ? (
                  <button type="button" className="ce-btn tk-delete" onClick={() => setConfirm(true)}>
                    Delete
                  </button>
                ) : null}
                <span className="ce-gap" />
                <button type="button" className={'ce-btn' + (t.writable ? '' : ' is-primary')} onClick={onClose}>
                  Close
                </button>
              </div>
            </>
          )}
        </div>
        {(t.history || []).length ? (
          <div className="ce-qr tk-hist">
            <span className="ce-label">DONE BEFORE</span>
            {t.history.slice(0, short ? 4 : 6).map((h, i) => (
              <span key={i} className="tk-hist-row">
                <span>{histDay(h.at)}</span>
                <span className="tk-hist-who">{h.who}</span>
              </span>
            ))}
          </div>
        ) : null}
      </div>
      {confirm ? (
        <div className="wt-overlay" role="dialog" aria-modal="true" aria-label="Delete the task?" onClick={(e) => e.target === e.currentTarget && setConfirm(false)}>
          <div className="wt-confirm">
            <div className="wt-sheet-title">Delete “{t.title}”?</div>
            <div className="wt-sheet-sub">It goes from {t.calendar} in GCloud, for everyone.</div>
            <div className="wt-confirm-btns">
              <button type="button" className="wt-confirm-btn" onClick={() => setConfirm(false)}>
                Cancel
              </button>
              <button type="button" className="wt-confirm-btn is-ok" disabled={busy} onClick={del}>
                Delete
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

/* ---------------- the form ---------------- */

// The Repeats choices (GCloud's words: "Every" + its repeat words); anything else is Custom.
const REPEATS = [
  { id: 'never', label: 'Never', rule: '' },
  { id: 'day', label: 'Every day', rule: 'FREQ=DAILY' },
  { id: 'week', label: 'Every week', rule: 'FREQ=WEEKLY' },
  { id: 'month', label: 'Every month', rule: 'FREQ=MONTHLY' },
  { id: '3months', label: 'Every 3 months', rule: 'FREQ=MONTHLY;INTERVAL=3' },
  { id: '6months', label: 'Every 6 months', rule: 'FREQ=MONTHLY;INTERVAL=6' },
  { id: 'year', label: 'Every year', rule: 'FREQ=YEARLY' },
];
const LEADS = [0, 1, 2, 3, 7, 14, 30];
const leadText = (n) => (n === 0 ? 'On the day' : n === 1 ? '1 day before' : `${n} days before`);
const repeatOf = (rule) => (REPEATS.find((r) => r.rule === (rule || '')) || { id: 'custom' }).id;

export function TaskForm({ task, day, calendars, people, short, onClose, onSaved }) {
  const cals = (calendars || []).filter((c) => c.writable);
  const editing = !!task;
  const start = (task && parseDay(task.due)) || day || new Date();
  const [title, setTitle] = useState((task && task.title) || '');
  const [calId, setCalId] = useState((task && task.calendarId) || (cals[0] && cals[0].id) || '');
  const [due, setDue] = useState(atMidnight(start));
  const [timed, setTimed] = useState(!!(task && task.dueAt));
  const [minutes, setMinutes] = useState(() => (task && task.dueAt ? Number(task.dueAt.slice(11, 13)) * 60 + Number(task.dueAt.slice(14, 16)) : 9 * 60));
  const [rule, setRule] = useState((task && task.rrule) || '');
  const [from, setFrom] = useState((task && task.repeatFrom) || 'due');
  // An owner who isn't one of Newman's people keeps their GCloud user name ("=").
  const [owner, setOwner] = useState(task ? task.ownerId || (task.ownerUser ? '=' : '') : '');
  const [askWho, setAskWho] = useState(!!(task && task.askWho));
  const [lead, setLead] = useState(task ? task.lead : 7);
  const [evening, setEvening] = useState(!!(task && task.eveningBefore));
  const [picker, setPicker] = useState(null); // 'date' | 'time' | 'custom' | 'untilDate'
  const [custom, setCustom] = useState(null);
  const [tried, setTried] = useState(false);
  const [saving, setSaving] = useState(false);

  const preset = repeatOf(rule);
  const noName = !title.trim();
  const blocked = noName || !calId;
  const cal = cals.find((c) => c.id === calId);
  const ownerName = owner === '=' ? task.ownerUser : owner ? (people.find((p) => p.id === owner) || {}).name || '' : '';
  const timeText = clock(Math.floor(minutes / 60), minutes % 60);

  const onRepeat = (id) => {
    if (id === 'custom') {
      setCustom({ ...customOf(rule || 'FREQ=WEEKLY', due), days: [] });
      setPicker('custom');
    } else setRule((REPEATS.find((r) => r.id === id) || REPEATS[0]).rule);
  };

  const save = async () => {
    if (blocked) {
      setTried(true);
      return;
    }
    setSaving(true);
    const body = {
      title: title.trim(),
      due: dateKey(due),
      time: timed ? `${pad(Math.floor(minutes / 60))}:${pad(minutes % 60)}` : '',
      rrule: rule,
      repeatFrom: rule ? from : 'due',
      owner,
      askWho: !owner && askWho,
      lead,
      eveningBefore: evening,
    };
    try {
      const out = editing
        ? await api.post('api/tasks/change', { id: task.id, task: body, ...(calId !== task.calendarId ? { calendar: calId } : {}) })
        : await api.post('api/tasks', { calendar: calId, ...body });
      toast(`${editing ? 'Saved to' : 'Added to'} ${out.calendar}`, 'success');
      onSaved();
      onClose();
    } catch (e) {
      setSaving(false);
      toast((e && e.message) || 'Couldn’t save it', 'error');
    }
  };

  const repeatSelect = (
    <select className="ce-input ce-select tk-wide" value={preset} onChange={(e) => onRepeat(e.target.value)} aria-label="Repeats">
      {REPEATS.map((r) => (
        <option key={r.id} value={r.id}>
          {r.label}
        </option>
      ))}
      <option value="custom">{preset === 'custom' && rule ? 'Custom: ' + everyWords(rule) : 'Custom…'}</option>
    </select>
  );
  const fromSelect = (
    <select className="ce-input ce-select tk-wide" value={rule ? from : ''} onChange={(e) => setFrom(e.target.value)} disabled={!rule} aria-label="Counted from">
      {!rule ? <option value="">—</option> : null}
      <option value="done">When it’s done</option>
      <option value="due">The due date</option>
    </select>
  );
  const ownerSelect = (
    <select className="ce-input ce-select" value={owner} onChange={(e) => setOwner(e.target.value)} aria-label="Belongs to">
      <option value="">Everyone</option>
      {owner === '=' ? <option value="=">{task.ownerUser}</option> : null}
      {people.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
  const leadSelect = (
    <select className="ce-input ce-select" value={lead} onChange={(e) => setLead(Number(e.target.value))} aria-label="Coming up">
      {(LEADS.includes(lead) ? LEADS : [...LEADS, lead].sort((a, b) => a - b)).map((n) => (
        <option key={n} value={n}>
          {leadText(n)}
        </option>
      ))}
    </select>
  );
  const check = (on, set, label) => (
    <label className="tk-check">
      <input type="checkbox" checked={on} onChange={(e) => set(e.target.checked)} />
      {label}
    </label>
  );
  const dueButtons = (
    <>
      <button type="button" className="ce-pick tk-date" onClick={() => setPicker('date')}>
        {shortDate(due)}
      </button>
      {timed ? (
        <button type="button" className="ce-pick ce-time tk-time" onClick={() => setPicker('time')}>
          {timeText}
        </button>
      ) : null}
    </>
  );
  const calSelect = (
    <select className="ce-input ce-select" value={calId} onChange={(e) => setCalId(e.target.value)} aria-label="Calendar">
      {cals.map((c) => (
        <option key={c.id} value={c.id}>
          {c.name}
        </option>
      ))}
    </select>
  );
  const whatInput = <input className="ce-input ce-what" value={title} onChange={(e) => setTitle(e.target.value)} placeholder="What needs doing?" aria-label="What" />;

  const summary = [
    title.trim() || 'Untitled',
    `due ${shortDate(due)}${timed ? ' at ' + timeText : ''}`,
    cal ? cal.name : '',
    rule ? `${everyWords(rule)}, counted from ${from === 'done' ? 'when it’s done' : 'the due date'}` : 'doesn’t repeat',
    ownerName || 'everyone',
  ]
    .filter(Boolean)
    .join(' · ');

  return (
    <div className={'wt-overlay ce-shade' + (short ? ' tk-full' : '')} role="dialog" aria-modal="true" aria-label={editing ? 'Edit task' : 'New task'}>
      <div className={'ce-form tk-form' + (short ? ' tk-form--short' : '')}>
        <div className="ce-form-head">
          <span className="ce-form-title">{editing ? 'Edit task' : 'New task'}</span>
          <span className="ce-gap" />
          <button type="button" className="ce-btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className={'ce-btn is-primary' + (blocked || saving ? ' is-off' : '')} aria-disabled={blocked || saving} onClick={() => !saving && save()}>
            {saving ? 'Saving…' : 'Save'}
          </button>
        </div>

        {short ? (
          <div className="tk-cols">
            <label className="tk-field">
              <span className="ce-label">WHAT</span>
              {whatInput}
            </label>
            <label className="tk-field">
              <span className="ce-label">REPEATS</span>
              {repeatSelect}
            </label>
            <label className="tk-field">
              <span className="ce-label">CALENDAR</span>
              {calSelect}
            </label>
            <label className="tk-field">
              <span className="ce-label">COUNTED FROM</span>
              {fromSelect}
            </label>
            <div className="tk-field">
              <span className="ce-label">DUE</span>
              <div className="tk-row">
                {dueButtons}
                {check(timed, setTimed, 'At a set time')}
              </div>
            </div>
            <label className="tk-field">
              <span className="ce-label">COMING UP</span>
              {leadSelect}
            </label>
            <div className="tk-field">
              <span className="ce-label">BELONGS TO</span>
              <div className="tk-row">
                {ownerSelect}
                {!owner ? check(askWho, setAskWho, 'Ask who did it') : null}
              </div>
            </div>
            <label className="tk-field">
              <span className="ce-label">HEADS-UP</span>
              <select className="ce-input ce-select" value={evening ? 'yes' : 'no'} onChange={(e) => setEvening(e.target.value === 'yes')} aria-label="Heads-up">
                <option value="yes">The evening before</option>
                <option value="no">None</option>
              </select>
            </label>
          </div>
        ) : (
          <>
            <div className="ce-top">
              <label className="ce-col">
                <span className="ce-label">WHAT</span>
                {whatInput}
              </label>
              <label className="ce-col ce-calcol">
                <span className="ce-label">CALENDAR</span>
                {calSelect}
              </label>
            </div>
            <div className="ce-grid tk-grid">
              <span className="ce-label">DUE</span>
              <div className="tk-row">{dueButtons}</div>
              {check(timed, setTimed, 'At a set time')}
              <span className="ce-label">REPEATS</span>
              {repeatSelect}
              <span className="ce-label">COUNTED FROM</span>
              {fromSelect}
              <span className="ce-label">BELONGS TO</span>
              {ownerSelect}
              {!owner ? check(askWho, setAskWho, 'Ask who did it') : <span />}
              <span className="ce-label">COMING UP</span>
              {leadSelect}
              {check(evening, setEvening, 'Heads-up the evening before')}
            </div>
          </>
        )}

        {tried && noName ? (
          <div className="ce-bad" role="alert">
            Say what needs doing first.
          </div>
        ) : null}
        {!short ? <div className="ce-summary">{summary}</div> : null}
      </div>

      {picker === 'date' || picker === 'untilDate' ? (
        <DatePicker
          value={picker === 'date' ? due : (custom && custom.until) || due}
          onPick={(d) => {
            if (picker === 'untilDate') {
              setCustom((c) => ({ ...c, until: d }));
              setPicker('custom');
            } else {
              setDue(atMidnight(d));
              setPicker(null);
            }
          }}
          onClose={() => setPicker(picker === 'untilDate' ? 'custom' : null)}
        />
      ) : null}
      {picker === 'time' ? (
        <TimePicker
          value={new Date(due.getFullYear(), due.getMonth(), due.getDate(), Math.floor(minutes / 60), minutes % 60)}
          onChange={setMinutes}
          onDone={() => setPicker(null)}
        />
      ) : null}
      {picker === 'custom' && custom ? (
        <CustomRepeat
          value={custom}
          forTask
          onChange={setCustom}
          onPickDate={() => setPicker('untilDate')}
          onDone={() => {
            setRule(customRule({ ...custom, days: [] }, true));
            setPicker(null);
          }}
        />
      ) : null}
    </div>
  );
}

