// The Today tile (approved TabletToday board): a greeting, To do (upkeep, devices needing a hand,
// appliance needs) with Done / Open right there, today's events, and heads-up. Everything comes
// from GET /api/today; tapping through goes to the To do, calendar and weather screens.

import { useState } from 'react';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { DeviceSheet } from './DeviceSheet.jsx';
import { dayKey } from './CalendarView.jsx';
import { PEOPLE_COLOURS, doItem, greeting, levelColour, undoItem, useRecentlyDone, useToday, useUpkeep, withRecent } from './today.js';

const HEADS_ICON = { warning: 'alert', rain: 'cloud', calendar: 'calendar', clock: 'clock' };
const HEADS_COLOUR = { warning: 'var(--wt-coral)', rain: '#7FB8FF', calendar: 'var(--wt-amber)', clock: 'var(--wt-amber)' };

function headsHref(h) {
  const t = new Date();
  if (h.source === 'weather') return '#/wall/weather';
  if (h.source === 'calendar') return '#/wall/calendar?day=' + dayKey(new Date(t.getFullYear(), t.getMonth(), t.getDate() + 1));
  return '#/wall/todo';
}

/** One To do row: stripe, title + status (tap: the To do screen), and its button. */
export function TodoRow({ item, people, onAsk, asking, onPick, onOpen, upkeep }) {
  const done = !!item.justDone;
  const doneText = item.action === 'dismiss' ? 'Done just now' : `Done just now${item.doneBy ? ' · ' + item.doneBy : ''}`;
  return (
    <div className={'td-row' + (done ? ' is-done' : '')}>
      <span className="td-stripe" style={{ background: done ? 'var(--wt-teal)' : levelColour(item.level) }} />
      {asking ? (
        <span className="td-who" role="group" aria-label={`Who did “${item.title}”?`}>
          <span className="td-who-q">Who?</span>
          {people.map((p, i) => (
            <button key={p.id} type="button" className="td-who-p" style={{ background: PEOPLE_COLOURS[i % PEOPLE_COLOURS.length] }} aria-label={`${p.name} did it`} onClick={() => onPick(item, p.name)}>
              {p.name.charAt(0).toUpperCase()}
            </button>
          ))}
          <button type="button" className="td-skip" onClick={() => onPick(item, null)}>
            Skip
          </button>
        </span>
      ) : (
        <a className="td-main" href={'#/wall/todo?item=' + encodeURIComponent(item.id)}>
          <span className="td-title">{item.title}</span>
          <span className="td-status" style={{ color: done ? 'var(--wt-teal)' : levelColour(item.level) }}>
            {done ? doneText : item.status}
            {!done && item.source === 'upkeep' && item.owner ? ' · ' + item.owner : ''}
          </span>
        </a>
      )}
      {asking ? null : done ? (
        item.source === 'upkeep' ? (
          <button type="button" className="td-btn td-btn--ghost" onClick={() => undoItem(item.id)}>
            Undo
          </button>
        ) : null
      ) : item.action === 'open' ? (
        <button type="button" className="td-btn td-btn--ghost" onClick={() => onOpen(item)}>
          Open
        </button>
      ) : (
        <button type="button" className="td-btn" onClick={() => (item.askWho && people.length ? onAsk(item) : doItem(item, upkeep, null))}>
          Done
        </button>
      )}
    </div>
  );
}

/** The device behind a "needs a hand" item, in the wall's device sheet. */
export function useDeviceOpener() {
  const { entitiesById, kindsById } = useHub();
  const [open, setOpen] = useState(null);
  const sheet = open && entitiesById[open.entity] ? (
    <DeviceSheet e={entitiesById[open.entity]} kind={kindsById[entitiesById[open.entity].kind]} main={null} note={open.status} onClose={() => setOpen(null)} />
  ) : null;
  return { openDevice: setOpen, sheet };
}

export default function TodayTile({ tile }) {
  const { data } = useToday();
  const upkeep = useUpkeep();
  const recent = useRecentlyDone();
  const [asking, setAsking] = useState(null);
  const { openDevice, sheet } = useDeviceOpener();
  const people = (upkeep.data && upkeep.data.people) || [];
  const limit = Math.max(1, Math.min(12, Number(tile.config.limit) || 4));
  if (!data) return <div className="wt-empty">Loading today…</div>;

  const todo = withRecent(data.todo, recent);
  const open = todo.filter((x) => !x.justDone);
  // Just-done rows stay in view (for Undo) even when the list is longer than the tile shows.
  const doneRows = todo.filter((x) => x.justDone);
  const soon = data.soon || [];
  const nowRows = [...open.slice(0, Math.max(1, limit - doneRows.length)), ...doneRows].slice(0, limit);
  // Room left under the limit shows what's coming up, rather than hiding it behind "+1 more".
  const soonRows = soon.slice(0, Math.max(0, limit - nowRows.length));
  const shown = [...nowRows, ...soonRows];
  const moreN = todo.length + soon.length - shown.length;
  const nextSoon = soon[soonRows.length];
  const moreText = moreN > 0 ? `+${moreN} more${nextSoon ? ` · ${nextSoon.title} ${String(nextSoon.status || '').toLowerCase()}` : ''}` : '';
  const events = data.events || [];
  const heads = data.headsUp || [];
  const summary = [open.length ? `${open.length} ${open.length === 1 ? 'thing' : 'things'} to do` : todo.length ? 'All done for today' : '', events.length ? `${events.length} ${events.length === 1 ? 'event' : 'events'}` : ''].filter(Boolean).join(' · ');
  const empty = !todo.length && !soon.length && !events.length && !heads.length;
  const pick = async (item, who) => {
    setAsking(null);
    await doItem(item, upkeep.data, who);
  };
  const today = dayKey(new Date());

  return (
    <div className="td">
      <div className="td-head">
        <span className="td-hello">{greeting()}</span>
        <span className="td-summary">{summary}</span>
      </div>
      <div className="td-body">
      {empty ? (
        <div className="td-empty">Nothing needs doing today.</div>
      ) : (
        <>
          {todo.length || soon.length ? (
            <>
              <a className="td-label" href="#/wall/todo">
                <span>To do</span>
                <span>See all ›</span>
              </a>
              {shown.map((item) => (
                <TodoRow key={item.id} item={item} people={people} upkeep={upkeep.data} asking={asking === item.id} onAsk={(it) => setAsking(it.id)} onPick={pick} onOpen={openDevice} />
              ))}
              {moreText ? (
                <a className="td-more" href="#/wall/todo">
                  {moreText} ›
                </a>
              ) : null}
            </>
          ) : null}
          {events.length ? (
            <>
              <a className="td-label" href={'#/wall/calendar?day=' + today}>
                <span>On today</span>
                <span>Calendar ›</span>
              </a>
              {events.slice(0, 3).map((ev, i) => (
                <a key={ev.start + ':' + i} className={'td-ev' + (ev.now ? ' is-now' : '')} href={'#/wall/calendar?day=' + today}>
                  <span className="td-ev-time">{ev.now ? 'Now' : ev.time}</span>
                  <span className="td-ev-bar" style={{ background: ev.colour }} />
                  <span className="td-ev-text">
                    <span className="td-ev-title">{ev.title}</span>
                    {ev.location ? <span className="td-ev-where">{ev.location}</span> : null}
                  </span>
                </a>
              ))}
            </>
          ) : null}
          {heads.length ? (
            <>
              <span className="td-label td-label--plain">Heads-up</span>
              {heads.slice(0, 3).map((h) => (
                <a key={h.id} className="td-heads" href={headsHref(h)}>
                  <span className="td-heads-icon" style={{ color: HEADS_COLOUR[h.icon] || 'var(--wt-dim)' }}>
                    <Icon name={HEADS_ICON[h.icon] || 'bell'} size={16} strokeWidth={2.2} />
                  </span>
                  <span>{h.text}</span>
                </a>
              ))}
            </>
          ) : null}
        </>
      )}
      </div>
      <a className="td-next" href="#/wall/calendar">
        Next 31 days <Icon name="chevronRight" size={16} strokeWidth={2.4} />
      </a>
      {sheet}
    </div>
  );
}

