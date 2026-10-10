// Household → Places (admins): the places that name where someone is. Home is built in (it starts at
// the weather connection's spot, 150 m) and can be moved or resized; others ("School") are added
// here. Click the map to put the place's centre there.

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading } from '../components/Controls.jsx';
import Household, { AddRow, HouseholdSplit } from '../components/Household.jsx';
import MapView from '../components/MapView.jsx';
import { Icon } from '../icons.jsx';

const NEW = '_new';

// The radius slider: 1000 fine steps on a log scale from 25 m to 5 km, so it drags smoothly at both
// ends. The value saved is rounded: to 5 m below 200 m, 10 m below 1 km, 50 m above.
const R_MIN = 25;
const R_MAX = 5000;
const STEPS = 1000;
const SPAN = Math.log(R_MAX / R_MIN);

function roundRadius(r) {
  const to = r < 200 ? 5 : r < 1000 ? 10 : 50;
  return Math.min(R_MAX, Math.max(R_MIN, Math.round(r / to) * to));
}
const radiusAt = (pos) => roundRadius(R_MIN * Math.exp((pos / STEPS) * SPAN));
const posOf = (r) => Math.max(0, Math.min(STEPS, Math.round((Math.log(Math.max(r, R_MIN) / R_MIN) / SPAN) * STEPS)));
/** 45 → "45 m", 1050 → "1.05 km", 2500 → "2.5 km": exactly what's saved. */
function radiusText(r) {
  if (r < 1000) return `${Math.round(r)} m`;
  return `${(r / 1000).toFixed(2).replace(/\.?0+$/, '')} km`;
}

/**
 * The slider keeps its own fine position while dragging (the radius it reports is rounded), and only
 * jumps when the radius changes from outside - another place picked.
 */
function RadiusSlider({ value, onChange }) {
  const [pos, setPos] = useState(() => posOf(value));
  useEffect(() => {
    if (radiusAt(pos) !== value) setPos(posOf(value));
  }, [value]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <input
      type="range"
      min={0}
      max={STEPS}
      step={1}
      value={pos}
      aria-label="Radius"
      aria-valuetext={radiusText(value)}
      onChange={(e) => {
        const p = Number(e.target.value);
        setPos(p);
        const r = radiusAt(p);
        if (r !== value) onChange(r);
      }}
    />
  );
}

export default function Places() {
  const { isAdmin } = useAuth();
  const confirm = useConfirm();
  const places = useApi('api/places');
  const [sel, setSel] = useState('home');
  const [draft, setDraft] = useState(null);
  const [busy, setBusy] = useState(false);
  const list = places.data || [];
  const current = sel === NEW ? null : list.find((p) => p.id === sel);

  // Start editing whatever is picked (a new place starts with no centre).
  useEffect(() => {
    if (sel === NEW) setDraft({ id: NEW, name: '', lat: null, lon: null, radiusM: 150 });
    else if (current) setDraft({ ...current });
  }, [sel, places.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const save = async () => {
    setBusy(true);
    try {
      const body = { lat: draft.lat, lon: draft.lon, radiusM: draft.radiusM, ...(draft.builtIn ? {} : { name: draft.name.trim() }) };
      const saved = draft.id === NEW ? await api.post('api/places', body) : await api.put('api/places/' + enc(draft.id), body);
      toast(`Saved ${saved.name}`, 'success');
      await places.reload();
      setSel(saved.id);
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    if (!(await confirm({ title: `Remove ${current.name}?`, body: 'People there will show their distance from home instead.', confirmLabel: 'Remove', danger: true }))) return;
    try {
      await api.del('api/places/' + enc(current.id));
      toast(`Removed ${current.name}`, 'success');
      setSel('home');
      places.reload();
    } catch {
      /* toast shown */
    }
  };

  const changed = draft && (draft.id === NEW || !current || ['name', 'lat', 'lon', 'radiusM'].some((k) => draft[k] !== current[k]));
  const ready = draft && typeof draft.lat === 'number' && (draft.builtIn || draft.name.trim());
  const shown = draft ? [...list.filter((p) => p.id !== draft.id), { ...draft, name: draft.name || 'New place' }] : list;

  return (
    <Household current="places">
      {!isAdmin ? (
        <div className="card">
          <Empty title="Only an admin can change places" />
        </div>
      ) : places.data === undefined ? (
        places.error ? <ErrorBox error={places.error} onRetry={places.reload} /> : <Loading />
      ) : (
        <HouseholdSplit
          listLabel="Places"
          list={
            <div className="rail-list">
              <AddRow label="Add a place" sub="School, work, the cottage…" selected={sel === NEW} onClick={() => setSel(NEW)} />
              {list.map((p) => (
                <button key={p.id} type="button" className="rail-row" aria-current={sel === p.id ? 'true' : undefined} onClick={() => setSel(p.id)}>
                  <span className="phone-icon" aria-hidden="true">
                    <Icon name={p.builtIn ? 'home' : 'pin'} size={16} />
                  </span>
                  <span className="rail-text">
                    <span className="rail-name">{p.name}</span>
                    <span className="rail-sub">{typeof p.lat === 'number' ? `${radiusText(p.radiusM)} around` : 'Not on the map yet'}</span>
                  </span>
                </button>
              ))}
            </div>
          }
        >
          <section className="pane map-pane" aria-label="Place on the map">
            {draft ? (
              <div className="place-bar">
                {draft.builtIn ? (
                  <span className="strong place-bar-name">Home</span>
                ) : (
                  <input className="input input--sm2 place-bar-name" placeholder="Name, e.g. School" aria-label="Place name" value={draft.name} maxLength={40} onChange={(e) => setDraft({ ...draft, name: e.target.value })} />
                )}
                <label className="place-radius">
                  <span className="muted small">Within</span>
                  <RadiusSlider value={draft.radiusM} onChange={(r) => setDraft((d) => ({ ...d, radiusM: r }))} />
                  <span className="strong small place-radius-val">{radiusText(draft.radiusM)}</span>
                </label>
                <span className="place-bar-actions">
                  {current && !current.builtIn ? (
                    <button type="button" className="icon-btn" aria-label={`Remove ${current.name}`} title="Remove place" onClick={remove}>
                      <Icon name="trash" size={16} />
                    </button>
                  ) : null}
                  <button type="button" className="btn btn-primary btn-sm2" disabled={busy || !changed || !ready} onClick={save}>
                    {draft.id === NEW ? 'Add place' : 'Save'}
                  </button>
                </span>
              </div>
            ) : null}
            <div className="place-hint muted small">{draft && typeof draft.lat !== 'number' ? 'Click the map where it is.' : 'Click the map to move its centre.'}</div>
            <MapView
              places={shown}
              highlight={draft && draft.id}
              focus={current && typeof current.lat === 'number' ? { lat: current.lat, lon: current.lon, key: current.id } : null}
              onMapClick={(lat, lon) => draft && setDraft({ ...draft, lat: +lat.toFixed(6), lon: +lon.toFixed(6) })}
              label="Map: click to set the place's centre"
            />
          </section>
        </HouseholdSplit>
      )}
    </Household>
  );
}
