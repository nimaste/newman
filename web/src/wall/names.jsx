// A dashboard's "Show as" names ({entityId: "Garage"}): inside a dashboard, every tile, list,
// chip, sheet and dialog shows a device by that name. Done once, by handing everything below a
// copy of the hub data with those names; the device's real name is unchanged everywhere else.

import { useMemo } from 'react';
import { HubContext, useHub } from '../hooks.js';

/** Names trimmed, blanks dropped, at most 40 characters: what the hub keeps. */
export function cleanNames(names) {
  const out = {};
  Object.entries(names || {}).forEach(([id, v]) => {
    const s = String(v || '').trim().slice(0, 40);
    if (s) out[id] = s;
  });
  return out;
}

export function NamedHub({ names, children }) {
  const hub = useHub();
  const value = useMemo(() => {
    const map = cleanNames(names);
    if (!Object.keys(map).length || !hub.entities) return hub;
    const entities = hub.entities.map((e) => (map[e.id] ? { ...e, name: map[e.id], realName: e.name } : e));
    return { ...hub, entities, entitiesById: Object.fromEntries(entities.map((e) => [e.id, e])) };
  }, [hub, names]);
  return <HubContext.Provider value={value}>{children}</HubContext.Provider>;
}
