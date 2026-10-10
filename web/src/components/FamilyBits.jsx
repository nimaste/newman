// React pieces of the family map (family.js has the logic): a person's avatar (their photo, else their
// initial on their colour) and asking the hub for someone's fresh location ("Updating…" until it comes).

import { useEffect, useRef, useState } from 'react';
import { api, enc } from '../api.js';
import { initial } from '../format.js';
import { colorOf, inkOn, photoSrc } from './family.js';

export function Avatar({ person, size = 32, className = '' }) {
  const [broken, setBroken] = useState(false);
  const src = broken ? null : photoSrc(person);
  useEffect(() => setBroken(false), [person && person.photo]);
  const color = colorOf(person);
  return (
    <span
      className={'fam-avatar ' + className}
      aria-hidden="true"
      style={{ width: size, height: size, background: color, color: inkOn(color), fontSize: Math.round(size * 0.44) }}
    >
      {src ? <img src={src} alt="" draggable={false} onError={() => setBroken(true)} /> : initial(person && person.name)}
    </span>
  );
}

const WAIT_MS = 45000; // stop showing "Updating…" if no fix came by then

/**
 * refresh(person): POST /api/people/<id>/refresh (the hub sends their phones one locate push, at most
 * every 2 minutes per person). busy(person): true from the tap until a newer fix arrives (or 45 s).
 */
export function useRefresher(people) {
  const [asked, setAsked] = useState({}); // person id -> {since (epoch s), until (ms)}
  const peopleRef = useRef(people);
  peopleRef.current = people;
  // A newer fix ends it; so does the timeout.
  useEffect(() => {
    const now = Date.now();
    const next = {};
    let changed = false;
    Object.entries(asked).forEach(([id, a]) => {
      const p = (people || []).find((x) => x.id === id);
      if (now > a.until || (p && p.at && p.at >= a.since)) changed = true;
      else next[id] = a;
    });
    if (changed) setAsked(next);
    const soonest = Math.min(...Object.values(next).map((a) => a.until));
    if (!isFinite(soonest)) return undefined;
    const t = setTimeout(() => setAsked((x) => ({ ...x })), Math.max(200, soonest - now + 50));
    return () => clearTimeout(t);
  }, [people, asked]);

  const refresh = (p) => {
    if (!p || !p.sharing) return;
    const mark = (since) => setAsked((x) => ({ ...x, [p.id]: { since, until: Date.now() + WAIT_MS } }));
    mark(Date.now() / 1000 - 5);
    api.post('api/people/' + enc(p.id) + '/refresh', {}, { quiet: true }).catch((e) => {
      // Someone asked a moment ago: still waiting for that answer, unless it already came.
      const next = e && e.data && e.data.nextAllowedAt;
      const cur = (peopleRef.current || []).find((x) => x.id === p.id);
      if (e && e.status === 429 && next && !(cur && cur.at >= next - 120)) mark(next - 120);
      else setAsked((x) => {
        const y = { ...x };
        delete y[p.id];
        return y;
      });
    });
  };
  const busy = (p) => !!(p && asked[p.id]);
  return { refresh, busy };
}
