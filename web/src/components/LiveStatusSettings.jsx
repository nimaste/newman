// A device's "Live status while…" (admins, on its page): while the device is in one of the ticked states,
// the chosen people's phones show a live status of it (iPhone Live Activity, Android ongoing
// notification) until it leaves them. The states come from the device's kind (entity.liveStatus.choices,
// worked out by the hub); each change is saved straight away.

import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { Check, Switch } from './Controls.jsx';

export function hasLiveStatus(e) {
  return !!(e && e.liveStatus && (e.liveStatus.choices || []).length > 1);
}

export default function LiveStatusSettings({ entity: e }) {
  const { setEntities } = useHub();
  const people = useApi('api/people');
  const [busy, setBusy] = useState(false);
  const ls = e.liveStatus || {};
  const states = ls.states || [];
  const everyone = ls.recipients === null || ls.recipients === undefined;
  const picked = everyone ? [] : ls.recipients;
  const list = people.data || [];

  const save = async (patch, done) => {
    setBusy(true);
    try {
      const updated = await api.put('api/entities/' + enc(e.id) + '/live-status', patch);
      if (updated && updated.id) setEntities((all) => (all || []).map((x) => (x.id === updated.id ? updated : x)));
      if (done) toast(done, 'success');
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false);
    }
  };
  const toggleState = (id) => {
    const next = states.includes(id) ? states.filter((s) => s !== id) : [...states, id];
    save({ states: next });
  };
  const togglePerson = (id) => save({ recipients: picked.includes(id) ? picked.filter((x) => x !== id) : [...picked, id] });
  const labels = (ls.choices || []).filter((c) => states.includes(c.id)).map((c) => c.label.toLowerCase());

  return (
    <div className="stack live-settings">
      <Switch
        checked={!!ls.enabled}
        disabled={busy}
        onChange={(on) => save({ enabled: on }, on ? `${e.name} shows a live status while ${labels.join(' or ') || '…'}` : `No live status for ${e.name}`)}
        label="Show a live status on phones"
      />
      <div className="field">
        <span className="field-label">While it’s</span>
        <div className="nsub-checks" role="group" aria-label="States that show it">
          {(ls.choices || []).map((c) => (
            <Check key={c.id} checked={states.includes(c.id)} disabled={busy} onChange={() => toggleState(c.id)}>
              {c.label}
            </Check>
          ))}
        </div>
      </div>
      <div className="field">
        <span className="field-label">Who sees it</span>
        <div className="nsub-checks" role="group" aria-label="Who sees it">
          <Check checked={everyone} disabled={busy} onChange={(v) => save({ recipients: v ? null : list.map((p) => p.id) })}>
            Everyone
          </Check>
          {!everyone
            ? list.map((p) => (
                <Check key={p.id} checked={picked.includes(p.id)} disabled={busy} onChange={() => togglePerson(p.id)}>
                  {p.name}
                </Check>
              ))
            : null}
        </div>
      </div>
      {ls.enabled && ls.showing ? <div className="help">Showing now.</div> : null}
    </div>
  );
}
