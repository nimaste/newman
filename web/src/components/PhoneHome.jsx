// Dashboards > Phone Home (admins): the household layout every phone's Home starts from - which cards
// (one per category, plus Today and People), their order, and up to 4 favourites - and, per person,
// whether they may change their own Home and a reset back to the household's.
// GET api/home/layouts · PUT api/home/household · POST api/home/people/{uid}/reset ·
// PATCH api/home/people/{uid} {canEditHome}.

import { useEffect, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useLive } from '../live.js';
import { Check, ErrorBox, Loading } from './Controls.jsx';
import { Icon } from '../icons.jsx';

const MAX_FAVOURITES = 4;

function Cards({ cards, setCards }) {
  const [drag, setDrag] = useState(null);
  const move = (from, to) => {
    if (to < 0 || to >= cards.length || from === to) return;
    const next = cards.slice();
    const [c] = next.splice(from, 1);
    next.splice(to, 0, c);
    setCards(next);
  };
  return (
    <ul className="ph-cards" aria-label="Cards">
      {cards.map((c, i) => (
        <li
          key={c.id}
          className={'ph-card' + (drag === i ? ' is-dragging' : '') + (c.on ? '' : ' is-off')}
          draggable
          onDragStart={() => setDrag(i)}
          onDragOver={(ev) => ev.preventDefault()}
          onDrop={() => (drag !== null ? move(drag, i) : null, setDrag(null))}
          onDragEnd={() => setDrag(null)}
        >
          <span className="ph-grip" aria-hidden="true">
            ≡
          </span>
          <span className="ph-card-name">
            {c.title}
            {c.new ? <span className="ph-new">NEW</span> : null}
          </span>
          <span className="ph-move">
            <button type="button" className="icon-btn" aria-label={`Move ${c.title} up`} disabled={i === 0} onClick={() => move(i, i - 1)}>
              <Icon name="up" size={14} />
            </button>
            <button type="button" className="icon-btn" aria-label={`Move ${c.title} down`} disabled={i === cards.length - 1} onClick={() => move(i, i + 1)}>
              <Icon name="down" size={14} />
            </button>
          </span>
          <Check checked={c.on} onChange={(on) => setCards(cards.map((x) => (x.id === c.id ? { ...x, on, new: false } : x)))}>
            <span className="sr-only">Show {c.title}</span>
          </Check>
        </li>
      ))}
    </ul>
  );
}

function Favourites({ favourites, setFavourites }) {
  const { entities, entitiesById, kindsById } = useHub();
  const nameOf = (id) => (entitiesById[id] && entitiesById[id].name) || id;
  // Something you can tap to do: a device whose kind has commands (lights, locks, the garage, groups…).
  const options = (entities || [])
    .filter((e) => !favourites.includes(e.id) && ((kindsById[e.kind] && kindsById[e.kind].commands) || []).length)
    .sort((a, b) => a.name.localeCompare(b.name));
  return (
    <div className="ph-favs">
      <div className="ph-fav-list">
        {favourites.length ? (
          favourites.map((id) => (
            <span key={id} className="ph-fav">
              {nameOf(id)}
              <button type="button" className="icon-btn" aria-label={`Remove ${nameOf(id)}`} onClick={() => setFavourites(favourites.filter((x) => x !== id))}>
                <Icon name="x" size={13} />
              </button>
            </span>
          ))
        ) : (
          <span className="muted">No favourites yet.</span>
        )}
      </div>
      {favourites.length < MAX_FAVOURITES ? (
        <select className="input input--sm" aria-label="Add a favourite" value="" onChange={(ev) => ev.target.value && setFavourites([...favourites, ev.target.value])}>
          <option value="">+ Add a favourite</option>
          {options.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name}
            </option>
          ))}
        </select>
      ) : null}
    </div>
  );
}

export default function PhoneHome() {
  const res = useApi('api/home/layouts');
  const users = useApi('api/users');
  const roleOf = Object.fromEntries((users.data || []).map((u) => [u.id, u.role]));
  const [cards, setCards] = useState(null);
  const [favourites, setFavourites] = useState([]);
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(null);

  useEffect(() => {
    if (!res.data || dirty) return;
    setCards(res.data.household.cards);
    setFavourites(res.data.household.favourites || []);
  }, [res.data]); // eslint-disable-line react-hooks/exhaustive-deps
  useLive((m) => {
    if (m.type === 'home' || m.type === 'categories') res.reload();
  });

  if (!res.data) return res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />;
  const edit = (fn) => (v) => {
    fn(v);
    setDirty(true);
  };
  const undo = () => {
    setDirty(false);
    setCards(res.data.household.cards);
    setFavourites(res.data.household.favourites || []);
  };
  const save = async () => {
    setSaving(true);
    try {
      const out = await api.put('api/home/household', { cards: cards.map((c) => ({ id: c.id, on: c.on })), favourites });
      setDirty(false);
      setCards(out.household.cards);
      setFavourites(out.household.favourites || []);
      res.setData(out);
      toast('Household layout saved', 'success');
    } catch {
      /* toast shown */
    } finally {
      setSaving(false);
    }
  };
  const person = async (p, fn) => {
    setBusy(p.userId);
    try {
      res.setData(await fn());
    } catch {
      /* toast shown */
    } finally {
      setBusy(null);
    }
  };
  const reset = (p) =>
    person(p, () => api.post('api/home/people/' + enc(p.userId) + '/reset', {})).then(() => toast(`${p.name} uses the household layout again`, 'success'));
  const setCanEdit = (p, on) => person(p, () => api.patch('api/home/people/' + enc(p.userId), { canEditHome: on }));

  return (
    <div className="ph-grid">
      <section className="ph-col" aria-label="Household layout">
        <div className="ph-head">
          <h2 className="ph-title">Household layout</h2>
          <p className="help">What everyone's Home shows, unless you let them change their own.</p>
        </div>
        <div className="ph-box">
          <div className="ph-box-label">Cards · drag to reorder</div>
          <Cards cards={cards || []} setCards={edit(setCards)} />
        </div>
        <div className="ph-box">
          <div className="ph-box-label">Favourites · up to {MAX_FAVOURITES}</div>
          <Favourites favourites={favourites} setFavourites={edit(setFavourites)} />
        </div>
        <div className="inline-form">
          <button type="button" className="btn btn-primary" disabled={!dirty || saving} onClick={save}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          {dirty ? (
            <button type="button" className="btn" onClick={undo}>
              Undo changes
            </button>
          ) : null}
        </div>
      </section>
      <section className="ph-col" aria-label="People">
        <div className="ph-head">
          <h2 className="ph-title">People</h2>
          <p className="help">Who may change their own Home, and who has.</p>
        </div>
        <ul className="ph-people">
          {res.data.people.map((p) => {
            const admin = roleOf[p.userId] === 'admin';
            return (
              <li key={p.userId} className="ph-person">
                <div className="ph-person-top">
                  <span className="ph-person-name">{p.name}</span>
                  <span className="ph-person-layout">
                    {p.layout === 'own' ? (
                      <>
                        <span className="ph-own">Own layout</span>
                        <button type="button" className="btn btn-sm" disabled={busy === p.userId} onClick={() => reset(p)}>
                          Reset to household
                        </button>
                      </>
                    ) : (
                      <span className="ph-household">Household layout</span>
                    )}
                  </span>
                </div>
                <Check checked={admin || p.canEditHome} disabled={admin || busy === p.userId} onChange={(on) => setCanEdit(p, on)}>
                  {admin ? 'Can edit their own Home (always, as an admin)' : 'Can edit their own Home'}
                </Check>
              </li>
            );
          })}
        </ul>
        <p className="help">When “Can edit their own Home” is off, their phone shows the household layout and has no Edit Home.</p>
      </section>
    </div>
  );
}
