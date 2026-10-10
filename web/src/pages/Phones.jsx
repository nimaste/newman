// Household → Phones: every phone that registered with the hub, listed by person on the left; the
// picked phone's details on the right. The "Developer: test phones" switch (bottom of the list,
// admins) adds a "Test phones" entry whose panel creates pretend phones and shows their outbox.

import { useState } from 'react';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { Empty, ErrorBox, Loading, Switch } from '../components/Controls.jsx';
import Household, { HouseholdSplit } from '../components/Household.jsx';
import { Icon } from '../icons.jsx';
import { fmtWhen, initial, platformLabel } from '../format.js';
import { InlineName, usePhoneActions } from './People.jsx';
import TestPhoneTools from './TestPhones.jsx';

const DEV_KEY = 'newman.phones.developer';
const DEV = '_dev';

function readDev() {
  try {
    return window.localStorage.getItem(DEV_KEY) === '1';
  } catch {
    return false;
  }
}

const phoneName = (ph) => ph.name || platformLabel(ph.platform);
const seenText = (ph) => (ph.lastSeen ? 'seen ' + fmtWhen(ph.lastSeen) : ph.registeredAt ? 'registered ' + fmtWhen(ph.registeredAt) : 'never seen');

/** The picked phone: rename, whose it is, forget, when it was last heard from. */
function PhoneDetail({ phone, people, onChanged }) {
  const { isAdmin } = useAuth();
  const { assign, rename, remove } = usePhoneActions(phone, people, onChanged);
  const owner = people.find((p) => p.id === phone.personId);
  return (
    <section className="pane" aria-label={phoneName(phone)}>
      <div className="conn-head">
        <span className="phone-icon phone-icon--lg" aria-hidden="true">
          <Icon name="phone" size={20} />
        </span>
        <div className="conn-head-text">
          <InlineName value={phoneName(phone)} label={`Rename ${phoneName(phone)}`} onSave={isAdmin ? rename : null} className="conn-title" />
          <div className="conn-status muted">
            {platformLabel(phone.platform)} · {seenText(phone)}
          </div>
        </div>
        {isAdmin ? (
          <button type="button" className="icon-btn" aria-label={`Forget ${phoneName(phone)}`} title="Forget phone" onClick={remove}>
            <Icon name="trash" size={18} />
          </button>
        ) : null}
      </div>
      <div className="pane-body">
        <dl className="hh-facts">
          <dt>
            <label htmlFor={'owner-' + phone.deviceId}>Belongs to</label>
          </dt>
          <dd>
            {isAdmin ? (
              <select id={'owner-' + phone.deviceId} className="select select--sm hh-fact-select" value={owner ? owner.id : ''} onChange={(e) => assign(e.target.value)}>
                <option value="">{owner ? 'Nobody (unassign)' : 'Nobody yet — choose…'}</option>
                {people.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            ) : owner ? (
              owner.name
            ) : (
              'Nobody yet'
            )}
          </dd>
          <dt>Type</dt>
          <dd>{platformLabel(phone.platform)}</dd>
          <dt>Last seen</dt>
          <dd>{phone.lastSeen ? fmtWhen(phone.lastSeen) : 'Not yet'}</dd>
          <dt>Registered</dt>
          <dd>{phone.registeredAt ? fmtWhen(phone.registeredAt) : '—'}</dd>
        </dl>
        {!owner ? <p className="help">An unassigned phone gets nothing until you choose whose it is.</p> : null}
      </div>
    </section>
  );
}

export default function Phones({ developer = false }) {
  // The test-phone tools are for admins only.
  const { isAdmin } = useAuth();
  const people = useApi('api/people', { interval: 10000 });
  const phones = useApi('api/phones', { interval: 5000 });
  const [dev, setDevState] = useState(() => developer || readDev());
  const [sel, setSel] = useState(developer ? DEV : null);
  const setDev = (v) => {
    setDevState(v);
    setSel(v ? DEV : null);
    try {
      window.localStorage.setItem(DEV_KEY, v ? '1' : '0');
    } catch {
      /* storage blocked: the toggle just isn't remembered */
    }
  };
  const reload = () => {
    people.reload();
    phones.reload();
  };

  const list = people.data || [];
  const all = phones.data || [];
  const groups = [
    ...list.map((p) => ({ id: p.id, name: p.name, phones: all.filter((ph) => ph.personId === p.id) })),
    { id: '_unassigned', name: 'Unassigned', phones: all.filter((ph) => !ph.personId || !list.some((p) => p.id === ph.personId)) },
  ].filter((g) => g.phones.length || g.id !== '_unassigned');
  const ordered = groups.flatMap((g) => g.phones);
  const showDev = dev && isAdmin;
  const selId = sel === DEV && showDev ? DEV : sel && all.some((ph) => ph.deviceId === sel) ? sel : ordered[0] ? ordered[0].deviceId : showDev ? DEV : null;
  const phone = all.find((ph) => ph.deviceId === selId);

  const loading = phones.data === undefined || people.data === undefined;
  const error = phones.error || people.error;

  return (
    <Household current="phones">
      {loading ? (
        error ? <ErrorBox error={error} onRetry={reload} /> : <Loading>Loading phones…</Loading>
      ) : (
        <HouseholdSplit
          listLabel="Phones"
          list={
            <>
              <div className="rail-list">
                {!all.length ? <Empty title="No phones yet">Install the Newman app on a phone and pick your name — it shows up here.</Empty> : null}
                {all.length
                  ? groups.map((g) => (
                      <div key={g.id} className="hh-group" role="group" aria-label={g.name}>
                        <div className="hh-group-title">
                          {g.id === '_unassigned' ? (
                            <span className="dot dot--amber" aria-hidden="true" />
                          ) : (
                            <span className="avatar avatar--xs avatar--on" aria-hidden="true">
                              {initial(g.name)}
                            </span>
                          )}
                          <span className="hh-group-name">{g.name}</span>
                          {!g.phones.length ? <span className="hh-group-none">no phones yet</span> : null}
                        </div>
                        {g.phones.map((ph) => (
                          <button key={ph.deviceId} type="button" className="rail-row" aria-current={selId === ph.deviceId ? 'true' : undefined} onClick={() => setSel(ph.deviceId)}>
                            <span className="phone-icon" aria-hidden="true">
                              <Icon name="phone" size={16} />
                            </span>
                            <span className="rail-text">
                              <span className="rail-name">{phoneName(ph)}</span>
                              <span className="rail-sub">
                                {platformLabel(ph.platform)} · {seenText(ph)}
                              </span>
                            </span>
                          </button>
                        ))}
                      </div>
                    ))
                  : null}
                {showDev ? (
                  <button type="button" className="rail-row rail-row--sep" aria-current={selId === DEV ? 'true' : undefined} onClick={() => setSel(DEV)}>
                    <span className="phone-icon" aria-hidden="true">
                      <Icon name="bolt" size={16} />
                    </span>
                    <span className="rail-text">
                      <span className="rail-name">Test phones</span>
                      <span className="rail-sub">Create pretend phones · outbox</span>
                    </span>
                  </button>
                ) : null}
              </div>
              {isAdmin ? (
                <div className="hh-list-foot">
                  <Switch className="hh-switch" checked={dev} onChange={setDev} label="Developer: test phones" />
                </div>
              ) : null}
            </>
          }
        >
          {selId === DEV ? (
            <TestPhoneTools people={people.data} phones={phones.data} onChanged={reload} />
          ) : phone ? (
            <PhoneDetail key={phone.deviceId} phone={phone} people={list} onChanged={reload} />
          ) : (
            <section className="pane" aria-label="No phone picked">
              <div className="pane-body">
                <Empty title="No phones yet">Install the Newman app on a phone and pick a name in it. The phone shows up in the list on the left.</Empty>
              </div>
            </section>
          )}
        </HouseholdSplit>
      )}
    </Household>
  );
}
