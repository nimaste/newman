import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useConfirm } from '../components/Feedback.jsx';
import { Empty, ErrorBox, Loading, MoreMenu } from '../components/Controls.jsx';
import { Icon } from '../icons.jsx';
import Household, { AddNameRow, HouseholdSplit } from '../components/Household.jsx';
import { useAuth } from '../auth.jsx';
import { fmtWhen, platformLabel, plural } from '../format.js';
import { Avatar } from '../components/FamilyBits.jsx';
import ProfileLook from '../components/ProfileLook.jsx';

function InlineName({ value, onSave, label, className }) {
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value);
  if (!onSave) return <span className={className}>{value}</span>;
  if (editing) {
    return (
      <input
        className="input input--sm"
        aria-label={label}
        value={draft}
        autoFocus
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          setEditing(false);
          const v = draft.trim();
          if (v && v !== value) onSave(v);
        }}
        onKeyDown={(e) => {
          if (e.key === 'Enter') e.currentTarget.blur();
          if (e.key === 'Escape') {
            setDraft(value);
            setEditing(false);
          }
        }}
      />
    );
  }
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2, minWidth: 0 }}>
      <span className={className}>{value}</span>
      <button
        type="button"
        className="icon-btn"
        aria-label={label}
        title="Rename"
        onClick={() => {
          setDraft(value);
          setEditing(true);
        }}
      >
        <Icon name="pencil" size={14} />
      </button>
    </span>
  );
}

export { InlineName };

/** Assign / rename / forget one phone (shared by the person detail and Household → Phones). */
export function usePhoneActions(phone, people, onChanged) {
  const confirm = useConfirm();
  const assign = async (personId) => {
    try {
      await api.patch('api/phones/' + enc(phone.deviceId), { personId: personId || null });
      const p = people.find((x) => x.id === personId);
      toast(p ? `${phone.name || 'Phone'} now belongs to ${p.name}` : `${phone.name || 'Phone'} unassigned`, 'success');
    } catch {
      /* toast shown */
    }
    onChanged();
  };
  const rename = async (name) => {
    try {
      await api.patch('api/phones/' + enc(phone.deviceId), { name });
    } catch {
      /* toast shown */
    }
    onChanged();
  };
  const remove = async () => {
    const ok = await confirm({
      title: `Forget ${phone.name || 'this phone'}?`,
      body: 'It stops getting notifications. If the app is opened again it registers itself as a new, unassigned phone.',
      confirmLabel: 'Forget phone',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del('api/phones/' + enc(phone.deviceId));
      toast('Phone removed', 'success');
    } catch {
      /* toast shown */
    }
    onChanged();
  };
  return { assign, rename, remove };
}

export function PhoneRow({ phone, people, onChanged }) {
  // Family accounts see phones but can't reassign, rename or forget them.
  const { isAdmin } = useAuth();
  const { assign, rename, remove } = usePhoneActions(phone, people, onChanged);
  return (
    <div className="phone-row">
      <span className="phone-icon" aria-hidden="true">
        <Icon name="phone" size={16} />
      </span>
      <div className="grow">
        <InlineName value={phone.name || platformLabel(phone.platform)} label={`Rename ${phone.name || 'phone'}`} onSave={isAdmin ? rename : null} className="strong" />
        <div className="phone-meta">
          {platformLabel(phone.platform)}
          {phone.lastSeen ? ' · seen ' + fmtWhen(phone.lastSeen) : phone.registeredAt ? ' · registered ' + fmtWhen(phone.registeredAt) : ''}
        </div>
      </div>
      {isAdmin ? (
      <>
      <label className="sr-only" htmlFor={'owner-' + phone.deviceId}>
        Whose phone is {phone.name || 'this'}?
      </label>
      <select id={'owner-' + phone.deviceId} className="select select--sm" value={phone.personId || ''} onChange={(e) => assign(e.target.value)}>
        <option value="">{phone.personId ? 'Unassign' : 'Assign to…'}</option>
        {people.map((p) => (
          <option key={p.id} value={p.id}>
            {p.name}
          </option>
        ))}
      </select>
      <button type="button" className="icon-btn" aria-label={`Forget ${phone.name || 'phone'}`} title="Forget phone" onClick={remove}>
        <Icon name="trash" size={16} />
      </button>
      </>
      ) : null}
    </div>
  );
}

export default function People() {
  const { isAdmin, user } = useAuth();
  const people = useApi('api/people', { interval: 10000 });
  const phones = useApi('api/phones', { interval: 10000 });
  const autos = useApi('api/automations');
  const confirm = useConfirm();
  const [adding, setAdding] = useState(false);
  const [sel, setSel] = useState(null);

  const reload = () => {
    people.reload();
    phones.reload();
  };

  const add = async (name) => {
    setAdding(true);
    try {
      const p = await api.post('api/people', { name });
      toast(`Added ${name}`, 'success');
      if (p && p.id) setSel(p.id);
      reload();
      return true;
    } catch {
      /* toast shown */
      return false;
    } finally {
      setAdding(false);
    }
  };

  const rename = async (p, name) => {
    try {
      await api.patch('api/people/' + enc(p.id), { name });
    } catch {
      /* toast shown */
    }
    reload();
  };

  const remove = async (p) => {
    const ok = await confirm({
      title: `Remove ${p.name}?`,
      body: `${p.name} stops getting alerts${(p.phones || []).length ? ', and their phones become unassigned' : ''}. Automations keep working for everyone else.`,
      confirmLabel: 'Remove',
      danger: true,
    });
    if (!ok) return;
    try {
      await api.del('api/people/' + enc(p.id));
      toast(`Removed ${p.name}`, 'success');
    } catch {
      /* toast shown */
    }
    reload();
  };

  const autoNames = Object.fromEntries((autos.data || []).map((a) => [a.id, a.name]));
  const list = people.data || [];
  const unassigned = (phones.data || []).filter((ph) => !ph.personId);
  const selId = sel && (sel === '_unassigned' || list.some((p) => p.id === sel)) ? sel : list[0] ? list[0].id : '_unassigned';
  const person = list.find((p) => p.id === selId);

  return (
    <Household current="people">
      {people.data === undefined ? (
        people.error ? <ErrorBox error={people.error} onRetry={people.reload} /> : <Loading>Loading people…</Loading>
      ) : (
        <HouseholdSplit
          listLabel="People"
          list={
            <div className="rail-list">
              {isAdmin ? <AddNameRow label="Add a person" sub="Someone who gets alerts" placeholder="Name, e.g. Marlina" busy={adding} onAdd={add} /> : null}
              {!list.length ? <Empty title="No people yet">Add the people who should get alerts, then pick their name in the Newman app on their phone.</Empty> : null}
              {list.map((p) => (
                <button key={p.id} type="button" className="rail-row" aria-current={selId === p.id ? 'true' : undefined} onClick={() => setSel(p.id)}>
                  <Avatar person={p} size={36} />
                  <span className="rail-text">
                    <span className="rail-name">{p.name}</span>
                    <span className="rail-sub">{(p.phones || []).length ? plural(p.phones.length, 'phone') : 'No phones yet'}</span>
                  </span>
                </button>
              ))}
              <button type="button" className="rail-row rail-row--sep" aria-current={selId === '_unassigned' ? 'true' : undefined} onClick={() => setSel('_unassigned')}>
                <span className={'dot ' + (unassigned.length ? 'dot--amber' : '')} aria-hidden="true" />
                <span className="rail-text">
                  <span className="rail-name">Unassigned phones</span>
                  <span className="rail-sub">{unassigned.length ? `${unassigned.length} waiting for an owner` : 'None'}</span>
                </span>
                <span className="rail-count">{unassigned.length}</span>
              </button>
            </div>
          }
        >
          {person ? (
            <section className="pane" aria-label={person.name}>
              <div className="conn-head">
                <Avatar person={person} size={48} />
                <div className="conn-head-text">
                  <InlineName value={person.name} label={`Rename ${person.name}`} onSave={isAdmin ? (n) => rename(person, n) : null} className="conn-title" />
                </div>
                {isAdmin ? <MoreMenu items={[{ label: `Remove ${person.name}`, danger: true, onClick: () => remove(person) }]} label={`More actions for ${person.name}`} /> : null}
              </div>
              <div className="pane-body person-body">
                {isAdmin || person.id === user.personId ? <ProfileLook person={person} mine={!isAdmin} onChanged={reload} /> : null}
                <div className="stack-sm">
                  <h2 className="group-title">Phones</h2>
                  {(person.phones || []).length ? (
                    <div className="phone-grid">
                      {person.phones.map((phone) => (
                        <PhoneRow key={phone.deviceId} phone={phone} people={list} onChanged={reload} />
                      ))}
                    </div>
                  ) : (
                    <div className="note">
                      Open the Newman app on {person.name}'s phone and choose “{person.name}” — or assign one from “Unassigned phones”.
                    </div>
                  )}
                </div>
                <div className="stack-sm">
                  <h2 className="group-title">Gets alerts from</h2>
                  {(() => {
                    const subs = (person.automations || []).filter((id) => autoNames[id] || !autos.data);
                    return subs.length ? (
                      <div className="link-grid">
                        {subs.map((id) => (
                          <a key={id} className="link-tile" href={'#/automations/' + enc(id)}>
                            <Icon name="bolt" size={15} /> {autoNames[id] || id}
                          </a>
                        ))}
                      </div>
                    ) : (
                      <div className="muted">No automations notify {person.name} yet.</div>
                    );
                  })()}
                </div>
              </div>
            </section>
          ) : (
            <section className="pane" aria-labelledby="unassigned-title">
              <div className="conn-head">
                <div className="conn-head-text">
                  <h2 id="unassigned-title" className="conn-title">
                    Unassigned phones
                  </h2>
                  <div className="conn-status muted">Phones that registered but don’t belong to anyone get nothing until you choose their owner.</div>
                </div>
              </div>
              <div className="pane-body">
                {phones.data === undefined ? (
                  phones.error ? <ErrorBox error={phones.error} onRetry={phones.reload} /> : <Loading />
                ) : !unassigned.length ? (
                  <Empty title="All set">Every phone belongs to someone.</Empty>
                ) : (
                  <div className="phone-grid">
                    {unassigned.map((phone) => (
                      <PhoneRow key={phone.deviceId} phone={phone} people={list} onChanged={reload} />
                    ))}
                  </div>
                )}
              </div>
            </section>
          )}
        </HouseholdSplit>
      )}
    </Household>
  );
}