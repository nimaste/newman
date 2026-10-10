// The Today page's own tiles (layout B: Today · Overview · …, the mornings' home page):
//   greeting  "Good morning" and today in one line - things to do, events, rain or a warning
//   todo      To do grouped like the To do screen (overdue, today, coming up), Done right there;
//             as many rows as the tile has room for, the rest behind "See all".
// Both read GET /api/today (today.js), like the Today tile.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
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

/** The element's height in px, kept current. */
function useHeight(ref) {
  const [h, setH] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el || typeof ResizeObserver === 'undefined') return undefined;
    const measure = () => setH(el.clientHeight);
    measure();
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, [ref]);
  return h;
}

// Row heights in wall.css (.tt-*): a row 44 + 6 gap, a group heading 30 + 6, "+N more" 30 + 6.
const ROW = 50;
const HEAD = 36;
const MORE = 36;

/** Which rows of each group fit in `height` px; the rest are counted for "+N more". */
export function fitGroups(groups, height) {
  const total = groups.reduce((n, g) => n + g.items.length, 0);
  const need = groups.reduce((px, g) => px + HEAD + g.items.length * ROW, 0);
  if (!height || need <= height) return { groups, hidden: 0 };
  let left = height - MORE;
  const out = [];
  for (const g of groups) {
    if (left < HEAD + ROW) break;
    left -= HEAD;
    const n = Math.min(g.items.length, Math.floor(left / ROW));
    out.push({ ...g, items: g.items.slice(0, n) });
    left -= n * ROW;
  }
  if (!out.length && groups.length) out.push({ ...groups[0], items: groups[0].items.slice(0, 1) }); // always one
  return { groups: out, hidden: total - out.reduce((n, g) => n + g.items.length, 0) };
}

export function TodoTile() {
  const { data } = useToday();
  const upkeep = useUpkeep();
  const recent = useRecentlyDone();
  const [asking, setAsking] = useState(null);
  const { openDevice, sheet } = useDeviceOpener();
  const bodyRef = useRef(null);
  const height = useHeight(bodyRef);
  const people = (upkeep.data && upkeep.data.people) || [];

  const all = data ? [...withRecent(data.todo, recent), ...(data.soon || [])] : [];
  const groups = GROUPS.map((g) => ({ ...g, items: all.filter((x) => g.levels.includes(x.level)) })).filter((g) => g.items.length);
  const fit = fitGroups(groups, height);
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
      <div className="tt-body" ref={bodyRef}>
        {!data ? <div className="wt-muted">Loading…</div> : null}
        {data && !all.length ? (
          <div className="tt-empty">
            <Icon name="check" size={20} strokeWidth={2.4} /> Nothing needs doing.
          </div>
        ) : null}
        {fit.groups.map((g) => (
          <div key={g.name} className="tt-group">
            <span className="tt-head">{g.name}</span>
            {g.items.map((item) => (
              <TodoRow key={item.id} item={item} people={people} upkeep={upkeep.data} asking={asking === item.id} onAsk={(it) => setAsking(it.id)} onPick={pick} onOpen={openDevice} />
            ))}
          </div>
        ))}
        {fit.hidden > 0 ? (
          <a className="tt-more" href="#/wall/todo">
            +{fit.hidden} more ›
          </a>
        ) : null}
      </div>
      {sheet}
    </div>
  );
}
