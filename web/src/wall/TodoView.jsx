// The wall's To do screen (#/wall/todo, approved TabletTodo board): Overdue / Today / Coming up as
// cards on the left, the chosen item on the right - Done (with "Who did it?" for shared items),
// Remind tomorrow, Undo, history; a device's problem opens the device; an appliance's need is
// dismissed. Data: GET /api/today (a task's facts and history come with it; GCloud Calendar keeps them)
// and GET /api/tasks/calendars for the household.

import { useState } from 'react';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { deviceIcon } from '../model.js';
import { useDeviceOpener } from './TodayTile.jsx';
import { ProblemList } from './MuteProblems.jsx';
import { PEOPLE_COLOURS, doItem, snooze, undoItem, useHousehold, useRecentlyDone, useToday, withRecent, failed } from './today.js';

export const GROUPS = [
  { name: 'Overdue', levels: ['overdue', 'device'] },
  { name: 'Today', levels: ['due', 'appliance'] },
  { name: 'Coming up', levels: ['soon'] },
];
const BADGE = {
  overdue: ['var(--wt-coral)', '#2a0e06'],
  device: ['var(--wt-coral)', '#2a0e06'],
  due: ['var(--wt-amber)', '#241a05'],
  appliance: ['var(--wt-amber)', '#241a05'],
  soon: ['rgba(238, 242, 255, 0.14)', 'var(--wt-ink)'],
};

const longDate = (iso) => {
  if (!iso) return '';
  const d = typeof iso === 'number' ? new Date(iso * 1000) : new Date(String(iso).length === 10 ? iso + 'T12:00:00' : iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleDateString([], { month: 'long', day: 'numeric', year: d.getFullYear() === new Date().getFullYear() ? undefined : 'numeric' });
};

function badgeText(item) {
  if (item.justDone) return 'Done';
  if (item.level === 'due' || item.level === 'appliance') return 'Today';
  if (item.level === 'device') return 'Needs a hand';
  return item.status;
}

function sourceText(item, e) {
  if (item.source === 'task') return (item.from || 'GCloud') + ' · ' + (item.owner || 'everyone');
  if (item.source === 'device') return 'Device · ' + ((e && (e.integrationName || e.name)) || 'device');
  return 'Appliance · ' + ((e && e.name) || 'appliance');
}

/** "6 months, from when it’s done" - how often a task comes back (its RRULE; "" when it doesn't). */
function everyText(item) {
  if (!item.every) return '';
  return item.every + (item.repeatFrom === 'done' ? ', from when it’s done' : '');
}

export default function TodoView({ itemId, onBack }) {
  const { entitiesById, kindsById } = useHub();
  const { data } = useToday();
  const household = useHousehold();
  const recent = useRecentlyDone();
  const [sel, setSel] = useState(itemId || null);
  const [asking, setAsking] = useState(false);
  const { openDevice, sheet } = useDeviceOpener();
  const people = (household.data && household.data.people) || [];

  const todo = data ? withRecent(data.todo, recent) : [];
  const soon = (data && data.soon) || [];
  const all = [...todo, ...soon];
  const current = all.find((x) => x.id === sel) || all[0] || null;
  const now = todo.filter((x) => !x.justDone).length;
  const e = current ? entitiesById[current.entity] : null;

  const done = async (who) => {
    setAsking(false);
    await doItem(current, who);
  };
  const remind = async () => {
    try {
      await snooze(current);
    } catch (err) {
      failed(err);
    }
  };

  let facts = [];
  let status = '';
  let statusColour = 'var(--wt-dim)';
  if (current) {
    status = current.justDone ? `Done just now${current.doneBy ? ' by ' + current.doneBy : ''}` : current.status;
    statusColour = current.justDone ? 'var(--wt-teal)' : current.level === 'soon' || !BADGE[current.level] ? 'var(--wt-dim)' : BADGE[current.level][0];
    if (current.source === 'task') {
      facts = [
        ['Belongs to', current.owner || 'Everyone'],
        ['Every', everyText(current)],
        ['Last done', current.justDone ? ['Today', current.doneBy].filter(Boolean).join(' · ') : [longDate(current.lastDone), current.lastDoneBy].filter(Boolean).join(' · ')],
        current.justDone ? ['Next due', longDate(current.nextDue)] : [current.level === 'overdue' ? 'Was due' : 'Due', longDate(current.due)],
      ].filter((f) => f[1]);
    } else if (current.source === 'device') {
      facts = [
        ['Device', e ? e.name : current.entity],
        ['Problem', current.status],
        ...(e && e.integrationName ? [['Reported by', e.integrationName]] : []),
      ];
    } else {
      facts = [
        ['Reported by', e ? `the ${e.name.toLowerCase()}` : 'the appliance'],
        ['Comes back', 'when it reports it again'],
      ];
    }
  }
  const past = current && current.source === 'task' && Array.isArray(current.history) ? current.history : [];
  // Just done here: this time first (GCloud has the line too; Today shows it once it's fetched again).
  const history = (current && current.justDone ? [{ at: new Date().toISOString(), who: current.doneBy || '' }, ...past] : past).slice(0, 4);

  return (
    <div className="wcal wtodo">
      <div className="wcal-main">
        <div className="wt-cams-head wcal-head">
          <button type="button" className="wt-back" onClick={onBack}>
            <Icon name="chevronLeft" size={26} strokeWidth={2.4} /> Back
          </button>
          <span className="wt-cams-title">To do</span>
          <span className="wt-dim-text">{data ? `${now} need doing now · ${soon.length} coming up` : 'Loading…'}</span>
        </div>
        <div className="wtodo-groups">
          {data && !all.length ? <div className="wcal-empty">Nothing needs doing.</div> : null}
          {GROUPS.map((g) => {
            const items = all.filter((x) => g.levels.includes(x.level));
            if (!items.length) return null;
            return (
              <div key={g.name} className="wtodo-group">
                <span className="wcal-heading">{g.name}</span>
                <div className="wtodo-cards">
                  {items.map((item) => {
                    const ie = entitiesById[item.entity];
                    const on = current && item.id === current.id;
                    const [bg, fg] = item.justDone ? ['var(--wt-teal)', '#04241e'] : BADGE[item.level] || BADGE.soon;
                    return (
                      <button key={item.id} type="button" className={'wtodo-card' + (on ? ' is-sel' : '') + (item.justDone ? ' is-done' : '')} aria-pressed={on} onClick={() => (setSel(item.id), setAsking(false))}>
                        <span className="wtodo-icon">
                          <Icon name={item.source === 'task' ? item.icon || 'wrench' : deviceIcon(ie, ie && kindsById[ie.kind])} size={24} />
                        </span>
                        <span className="wtodo-text">
                          <span className="wtodo-title">{item.title}</span>
                          <span className="wtodo-sub">
                            <span className="wtodo-badge" style={{ background: bg, color: fg }}>
                              {badgeText(item)}
                            </span>
                            <span className="wtodo-src">{sourceText(item, ie)}</span>
                          </span>
                        </span>
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </div>

      <aside className="wcal-side wtodo-side" aria-label="Item">
        {!current ? (
          <div className="wcal-empty">Nothing to show.</div>
        ) : (
          <>
            <div className="wcal-side-head">
              <span className="wcal-side-title">{current.title}</span>
              <span className="wtodo-status" style={{ color: statusColour }}>
                {status}
              </span>
            </div>
            {facts.length ? (
              <div className="wtodo-facts">
                {facts.map(([k, v]) => (
                  <div key={k} className="wtodo-fact">
                    <span>{k}</span>
                    <span>{v}</span>
                  </div>
                ))}
              </div>
            ) : null}
            {current.source === 'task' ? (
              asking ? (
                <div className="wtodo-who">
                  <span className="wtodo-who-q">Who did it?</span>
                  <div className="wtodo-who-grid">
                    {people.map((p, i) => (
                      <button key={p.id} type="button" className="wtodo-person" aria-label={`${p.name} did it`} onClick={() => done(p.name)}>
                        <span className="wtodo-circle" style={{ background: PEOPLE_COLOURS[i % PEOPLE_COLOURS.length] }}>
                          {p.name.charAt(0).toUpperCase()}
                        </span>
                        <span className="wtodo-pname">{p.name}</span>
                      </button>
                    ))}
                  </div>
                  <button type="button" className="wtodo-skip" onClick={() => done(null)}>
                    Skip · just mark it done
                  </button>
                </div>
              ) : (
                <>
                  <div className="wtodo-btns">
                    {current.justDone ? (
                      <button type="button" className="wtodo-btn" onClick={() => undoItem(current.id)}>
                        Undo
                      </button>
                    ) : (
                      <button type="button" className="wtodo-btn is-main" onClick={() => (current.askWho && people.length ? setAsking(true) : done(null))}>
                        Done
                      </button>
                    )}
                    <button type="button" className="wtodo-btn" disabled={current.justDone} onClick={remind}>
                      Tomorrow
                    </button>
                  </div>
                  {current.owner && !current.justDone ? <span className="wtodo-note">Belongs to {current.owner}, so Done credits {current.owner}.</span> : null}
                </>
              )
            ) : current.source === 'device' ? (
              <>
                <button type="button" className="wtodo-btn is-wide" onClick={() => openDevice(current)}>
                  Open the device
                </button>
                <span className="wtodo-note">This one clears by itself once the device is fine again.</span>
                {e ? <ProblemList entity={e} /> : null}
              </>
            ) : current.justDone ? (
              <span className="wtodo-note">Done. It comes back if the appliance reports it again.</span>
            ) : (
              <button type="button" className="wtodo-btn is-main is-wide" onClick={() => done(null)}>
                Done
              </button>
            )}
            {history.length ? (
              <>
                <span className="wcal-heading">Done before</span>
                {history.map((h, i) => (
                  <div key={i} className="wtodo-hist">
                    <span>{longDate(h.at)}</span>
                    <span>{h.who}</span>
                  </div>
                ))}
              </>
            ) : null}
          </>
        )}
        <span className="wtodo-gap" />
        <span className="wtodo-foot">Add or change tasks in GCloud Calendar, on the website or the GCloud Mail app.</span>
      </aside>
      {sheet}
    </div>
  );
}
