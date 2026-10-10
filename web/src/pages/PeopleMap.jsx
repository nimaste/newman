// Household → Map: where the household is, for those the signed-in person may see (the hub decides:
// guests see nobody; a wall tablet sees people only with its account's "Can see where people are").
// Home / a named place / distance from home, the same words as the phone app. Like the wall's Family
// map: people at one place share a bubble, the map keeps to the home area (anyone over ~50 km away is
// listed with how far and which way, and "Show everyone" fits them all), and picking someone asks the
// hub for their fresh location (one push to their phone, at most every 2 minutes) and flies to them.
// Moves arrive over the live socket.

import { useEffect, useMemo, useState } from 'react';
import { useApi } from '../hooks.js';
import { useLive } from '../live.js';
import { useAuth } from '../auth.jsx';
import { Empty, ErrorBox, Loading } from '../components/Controls.jsx';
import Household, { HouseholdSplit } from '../components/Household.jsx';
import MapView from '../components/MapView.jsx';
import { Avatar, useRefresher } from '../components/FamilyBits.jsx';
import { FLY_SPAN_M, arrowStyle, boundsAround, boundsOf, groupPeople, homeArea, located, spanOf } from '../components/family.js';
import { fmtAgo, fmtDistance, whereText } from '../format.js';

export default function PeopleMap() {
  const { user } = useAuth();
  const people = useApi('api/people', { interval: 120000 });
  const places = useApi('api/places');
  const [sel, setSel] = useState(null);
  const [everyone, setEveryone] = useState(false);
  const [, tick] = useState(0);

  // A person who moved (or started / stopped sharing, or changed their colour or photo) replaces their copy.
  useLive((m) => {
    if (m.type === 'reconnected') people.reload();
    if (m.type !== 'person' || !m.person) return;
    people.setData((list) => (Array.isArray(list) ? list.map((p) => (p.id === m.person.id ? { ...p, ...m.person } : p)) : list));
  });

  // "4 min ago" stays true.
  useEffect(() => {
    const t = setInterval(() => tick((n) => n + 1), 30000);
    return () => clearInterval(t);
  }, []);

  const list = (people.data || []).slice().sort((a, b) => located(b) - located(a));
  const { refresh, busy } = useRefresher(list);
  const person = list.find((p) => p.id === sel && located(p));
  const home = (places.data || []).find((x) => x.id === 'home');
  const area = useMemo(() => homeArea(list, home), [people.data, home]); // eslint-disable-line react-hooks/exhaustive-deps
  const farOf = (p) => area.far.find((f) => f.person.id === p.id);
  const all = list.filter(located);
  const wide = everyone || !!person;
  let focus = null;
  if (person) focus = { bounds: boundsAround(person.lat, person.lon, FLY_SPAN_M, 0.6), key: `p:${person.id}:${person.lat},${person.lon}` };
  else if (everyone && all.length) focus = { bounds: boundsOf(all), key: 'all' };
  else if (area.bounds) focus = { bounds: area.bounds, key: 'home:' + JSON.stringify(area.bounds) };
  const groups = groupPeople(wide ? all : area.near, all.length, everyone && !person && focus ? Math.max(150, spanOf(focus.bounds) * 0.04) : undefined);
  const mapPlaces = (places.data || []).filter((pl) => !groups.some((g) => g.place && g.place === pl.name));

  const pick = (id) => {
    const p = list.find((x) => x.id === id);
    if (sel === id) {
      setSel(null);
      return;
    }
    refresh(p);
    setSel(id);
  };
  const onPin = (id) => {
    const g = groups.find((x) => x.id === id);
    if (g && g.members.length === 1) pick(g.members[0].id);
  };

  return (
    <Household current="map">
      {people.data === undefined ? (
        people.error ? <ErrorBox error={people.error} onRetry={people.reload} /> : <Loading>Loading…</Loading>
      ) : (
        <HouseholdSplit
          listLabel="Where everyone is"
          list={
            <div className="rail-list">
              {!list.length ? <Empty title="Nobody to show">Only household members can see where people are.</Empty> : null}
              {list.map((p) => {
                const on = located(p);
                const me = p.id === user.personId;
                const far = farOf(p);
                return (
                  <button key={p.id} type="button" className={'rail-row' + (on ? '' : ' is-quiet')} aria-current={person && person.id === p.id ? 'true' : undefined} disabled={!on} onClick={() => pick(p.id)}>
                    <Avatar person={p} size={36} className={on ? '' : 'is-dim'} />
                    <span className="rail-text">
                      <span className="rail-name">
                        {p.name}
                        {me ? <span className="muted small"> (you)</span> : null}
                      </span>
                      <span className="rail-sub">
                        {far && !p.place ? `${fmtDistance(far.distanceM)} from home ` : whereText(p)}
                        {far ? <span style={arrowStyle(far.bearing)} aria-hidden="true">↑</span> : null}
                        {busy(p) ? ' · Updating…' : on && p.at ? ` · ${fmtAgo(p.at)}` : ''}
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          }
        >
          <section className="pane map-pane" aria-label="Map">
            <MapView places={mapPlaces} people={groups} focus={focus} onPersonClick={onPin} label="Map of where everyone is" />
            {area.far.length || wide ? (
              <div className="pmap-tools">
                {person ? (
                  <button type="button" className="btn btn-sm" onClick={() => setSel(null)}>
                    Back to the home area
                  </button>
                ) : (
                  <button type="button" className="btn btn-sm" onClick={() => setEveryone((x) => !x)}>
                    {everyone ? 'Back to the home area' : `Show everyone (${area.far.length} farther away)`}
                  </button>
                )}
              </div>
            ) : null}
          </section>
        </HouseholdSplit>
      )}
    </Household>
  );
}
