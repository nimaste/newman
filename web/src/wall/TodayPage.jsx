// The Today page's own tiles (layout B: Today · Overview · …, the mornings' home page):
//   greeting  "Good morning" and today in one line - things to do, events, rain or a warning
//   todo      To do grouped like the To do screen (overdue, today, coming up), Done right there;
//             every row, scrolling when the tile is short.
// Both read GET /api/today (today.js), like the Today tile.

import { useEffect, useRef, useState } from 'react';
import { useMoreAfter } from './fade.js';
import { Icon } from '../icons.jsx';
import { TodoRow, useDeviceOpener } from './TodayTile.jsx';
import { GROUPS } from './TodoView.jsx';
import { doItem, greeting, useRecentlyDone, useToday, useUpkeep, withRecent } from './today.js';

const plural = (n, one, many) => `${n} ${n === 1 ? one : many}`;

/** "3 things to do · 3 events today · rain from 3 PM" */
export function summaryLine(data, recent) {
  const open = withRecent(data.todo, recent).filter((x) => !x.justDone).length;
  const events = (data.events || []).length;
  const weather = (data.headsUp || []).filter((h) => h.source === 'weather');
  // The most serious official alert: a warning before a watch, an advisory or a statement.
  const rank = (h) => ['warning', 'watch', 'advisory'].indexOf(h.level) + 1 || 9;
  const warning = weather.filter((h) => h.icon === 'warning').sort((a, b) => rank(a) - rank(b))[0];
  const rain = weather.find((h) => h.icon === 'rain');
  return [
    open ? plural(open, 'thing to do', 'things to do') : 'Nothing to do',
    events ? plural(events, 'event today', 'events today') : '',
    warning ? warning.short || String(warning.text || '').toLowerCase() : '',
    rain ? rain.short || String(rain.text || '').toLowerCase() : '',
  ]
    .filter(Boolean)
    .join(' · ');
}

function useMinute() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);
  return now;
}

export function GreetingTile() {
  const { data } = useToday();
  const recent = useRecentlyDone();
  const now = useMinute();
  return (
    <div className="tg">
      <span className="tg-hello">{greeting(now)}</span>
      {data ? (
        <a className="tg-line" href="#/wall/todo">
          {summaryLine(data, recent)}
        </a>
      ) : null}
    </div>
  );
}

export function TodoTile() {
  const { data } = useToday();
  const upkeep = useUpkeep();
  const recent = useRecentlyDone();
  const [asking, setAsking] = useState(null);
  const { openDevice, muteDevice, sheet } = useDeviceOpener();
  const bodyRef = useRef(null);
  const more = useMoreAfter(bodyRef);
  const people = (upkeep.data && upkeep.data.people) || [];

  const all = data ? [...withRecent(data.todo, recent), ...(data.soon || [])] : [];
  const groups = GROUPS.map((g) => ({ ...g, items: all.filter((x) => g.levels.includes(x.level)) })).filter((g) => g.items.length);
  const pick = async (item, who) => {
    setAsking(null);
    await doItem(item, upkeep.data, who);
  };

  return (
    <div className="tt">
      <div className="wt-tile-head">
        <span>To do</span>
        <a className="wt-link" href="#/wall/todo">
          See all ›
        </a>
      </div>
      <div className={'tt-body wt-scroll' + (more ? ' has-more' : '')} ref={bodyRef}>
        {!data ? <div className="wt-muted">Loading…</div> : null}
        {data && !all.length ? (
          <div className="tt-empty">
            <Icon name="check" size={20} strokeWidth={2.4} /> Nothing needs doing.
          </div>
        ) : null}
        {groups.map((g) => (
          <div key={g.name} className="tt-group">
            <span className="tt-head">{g.name}</span>
            {g.items.map((item) => (
              <TodoRow key={item.id} item={item} people={people} upkeep={upkeep.data} asking={asking === item.id} onAsk={(it) => setAsking(it.id)} onPick={pick} onOpen={openDevice} onMute={muteDevice} />
            ))}
          </div>
        ))}
      </div>
      {sheet}
    </div>
  );
}
