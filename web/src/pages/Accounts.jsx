// Household → Accounts (admins): who can sign in to Newman. People without an account still get
// notifications; they just can't sign in. A "Wall tablet" account is a tablet on the wall: its own
// dashboard, never notified, can't change settings - it signs in only on the tablet. Its "Can see
// where people are" (on by default) decides whether its People tile shows anyone.

// A guest uses the house like family but isn't one of the people: never notified, never on People,
// can't see where anyone is.

const ROLE_WORD = { admin: 'an admin', family: 'family', guest: 'a guest', tablet: 'a wall tablet' };
const ROLE_LABEL = { admin: 'Admin', family: 'Family', guest: 'Guest', tablet: 'Wall tablet' };
const ADD = '_add';

import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { useConfirm } from '../components/Feedback.jsx';
import { Check, Empty, ErrorBox, Loading, Modal, MoreMenu, Switch } from '../components/Controls.jsx';
import Household, { AddRow, HouseholdSplit } from '../components/Household.jsx';
import { Icon } from '../icons.jsx';
import { fmtWhen, initial } from '../format.js';
import VoiceSettings from './VoiceSettings.jsx';
import SmartCharging from './SmartCharging.jsx';
import ConnectDevice from '../components/ConnectDevice.jsx';

function ResetPassword({ user, onClose, onDone }) {
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      await api.patch('api/users/' + enc(user.id), { password }, { quiet: true });
      toast(`New password set for ${user.name}. They were signed out everywhere.`, 'success');
      onDone();
      onClose();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Reset ${user.name}'s password`} onClose={onClose}>
      <form className="stack-sm" onSubmit={submit}>
        <p className="help">Give them this temporary password; they can change it under their profile.</p>
        <input className="input" type="text" autoComplete="new-password" placeholder="Temporary password" aria-label="Temporary password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus />
        {error ? <div className="auth-error" role="alert">{error}</div> : null}
        <div className="inline-form">
          <button type="submit" className="btn btn-primary" disabled={busy || !password}>
            Set password
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

const LOC_MODES = [
  { id: 'off', label: 'Off' },
  { id: 'choice', label: 'Their choice' },
  { id: 'required', label: 'Required' },
];

/**
 * Who may see a "Required" account: everyone in the household (and every wall tablet allowed to see
 * people), or the people and wall tablets ticked.
 */
function WhoSees({ user, people, tablets, onClose, onSave }) {
  const others = [...people.filter((p) => p.id !== user.personId), ...tablets.map((t) => ({ id: t.id, name: t.name, tablet: t }))];
  const [picked, setPicked] = useState(user.location.visibleTo || others.map((p) => p.id));
  const toggle = (id, on) => setPicked((cur) => (on ? [...cur, id] : cur.filter((x) => x !== id)));
  const everyone = others.every((p) => picked.includes(p.id));
  return (
    <Modal
      title={`Who can see where ${user.name} is`}
      onClose={onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn btn-primary" onClick={() => onSave(everyone ? null : picked.filter((id) => others.some((p) => p.id === id)))}>
            Save
          </button>
        </>
      }
    >
      <div className="stack-sm">
        <p className="help">{user.name} always shares their location and can't turn it off. Only the people and wall tablets ticked here see it.</p>
        {others.map((p) => (
          <Check key={p.id} checked={picked.includes(p.id)} onChange={(on) => toggle(p.id, on)}>
            {p.name}
            {p.tablet ? <span className="muted small"> · wall tablet{p.tablet.seesPeople ? '' : ' (its “Can see where people are” is off)'}</span> : null}
          </Check>
        ))}
      </div>
    </Modal>
  );
}

/** An account's location sharing: Off / Their choice / Required (+ who sees them). */
function LocationSharing({ user, people, tablets, onPatch }) {
  const [choosing, setChoosing] = useState(false);
  const loc = user.location || { mode: 'off' };
  const names = Object.fromEntries([...people, ...tablets].map((p) => [p.id, p.name]));
  const seenBy = loc.visibleTo === null || loc.visibleTo === undefined ? 'everyone' : loc.visibleTo.map((id) => names[id]).filter(Boolean).join(', ') || 'nobody';
  return (
    <div className="acct-loc">
      <select id={'loc-' + user.id} className="select select--sm acct-loc-select" value={loc.mode} onChange={(e) => onPatch({ location: { mode: e.target.value } }, `Location sharing for ${user.name}: ${LOC_MODES.find((m) => m.id === e.target.value).label.toLowerCase()}`)}>
        {LOC_MODES.map((m) => (
          <option key={m.id} value={m.id}>
            {m.label}
          </option>
        ))}
      </select>
      <span className="muted small acct-loc-state">
        {loc.mode === 'choice' ? (loc.enabled ? 'sharing now' : 'not shared yet — they turn it on in their app') : null}
        {loc.mode === 'off' ? 'not shared' : null}
      </span>
      {loc.mode === 'required' ? (
        <button type="button" className="link-btn small" onClick={() => setChoosing(true)}>
          Seen by {seenBy}
        </button>
      ) : null}
      {choosing ? (
        <WhoSees
          user={user}
          people={people}
          tablets={tablets}
          onClose={() => setChoosing(false)}
          onSave={(visibleTo) => {
            setChoosing(false);
            onPatch({ location: { visibleTo } }, `Saved who can see ${user.name}`);
          }}
        />
      ) : null}
    </div>
  );
}

function AddAccount({ onDone }) {
  const [name, setName] = useState('');
  const [username, setUsername] = useState('');
  const [role, setRole] = useState('family');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const suggested = name.trim().toLowerCase().replace(/[^a-z0-9._-]+/g, '').slice(0, 32);
  const submit = async (e) => {
    e.preventDefault();
    setBusy(true);
    setError('');
    try {
      const u = await api.post('api/users', { name: name.trim(), username: (username || suggested).trim().toLowerCase(), role, password }, { quiet: true });
      toast(`Added ${u.name}`, 'success');
      setName('');
      setUsername('');
      setPassword('');
      setRole('family');
      onDone(u);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <section className="pane" aria-labelledby="add-acct">
      <div className="conn-head">
        <span className="phone-icon phone-icon--lg" aria-hidden="true">
          <Icon name="plus" size={20} />
        </span>
        <div className="conn-head-text">
          <h2 id="add-acct" className="conn-title">
            Add an account
          </h2>
          <div className="conn-status muted">Someone who can sign in to Newman, or a wall tablet.</div>
        </div>
      </div>
      <div className="pane-body">
      <form className="stack-sm hh-form" onSubmit={submit}>
        <input className="input" placeholder={role === 'tablet' ? 'Name, e.g. Hallway tablet' : 'Name, e.g. Marlina'} aria-label="Name" value={name} onChange={(e) => setName(e.target.value)} required />
        <input className="input" placeholder={suggested || 'Username'} aria-label="Username" autoCapitalize="none" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} />
        <select className="select" aria-label="Role" value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="family">Family — can see everything and control devices</option>
          <option value="admin">Admin — can also change settings, automations and accounts</option>
          <option value="guest">Guest — controls devices, isn't on People, can't see where anyone is</option>
          <option value="tablet">Wall tablet — its own dashboard, never notified, can't change settings</option>
        </select>
        <input className="input" type="text" autoComplete="new-password" placeholder={role === 'tablet' ? 'Password (you type it once on the tablet)' : 'Temporary password'} aria-label="Temporary password" value={password} onChange={(e) => setPassword(e.target.value)} required />
        {role === 'tablet' ? <p className="help">Sign in with this account on the tablet. Give it a dashboard under Dashboards.</p> : null}
        {error ? <div className="auth-error" role="alert">{error}</div> : null}
        <button type="submit" className="btn btn-primary prof-btn" disabled={busy || !name.trim() || !password}>
          <Icon name="plus" size={16} /> Add account
        </button>
      </form>
      <p className="help" style={{ marginTop: 16 }}>
        People without an account (under People) still get the notifications they're set up for — they just can't sign in.
      </p>
      </div>
    </section>
  );
}

/** Rename an account (a wall tablet's name is what its battery and music player are called). */
function RenameAccount({ user, patch, onClose }) {
  const [name, setName] = useState(user.name);
  const [username, setUsername] = useState(user.username || '');
  const [busy, setBusy] = useState(false);
  const submit = async (e) => {
    e.preventDefault();
    const n = name.trim();
    const un = username.trim().toLowerCase();
    const changes = {};
    if (n && n !== user.name) changes.name = n;
    if (un && un !== user.username) changes.username = un;
    if (!Object.keys(changes).length) return onClose();
    setBusy(true);
    try {
      await patch(user, changes, changes.username ? `Signs in as ${changes.username} now` : `Renamed to ${n}`);
      onClose();
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={`Rename ${user.name}`} onClose={onClose}>
      <form className="stack-sm" onSubmit={submit}>
        <label className="field-label" htmlFor="rename-name">Name</label>
        <input id="rename-name" className="input" maxLength={48} placeholder={user.role === 'tablet' ? 'e.g. Kitchen tablet' : 'Name'} value={name} onChange={(e) => setName(e.target.value)} autoFocus />
        <label className="field-label" htmlFor="rename-username">Username (to sign in)</label>
        <input id="rename-username" className="input" maxLength={32} autoCapitalize="none" autoCorrect="off" spellCheck={false} value={username} onChange={(e) => setUsername(e.target.value)} />
        <div className="inline-form">
          <button type="submit" className="btn btn-primary" disabled={busy || !name.trim()}>
            Rename
          </button>
          <button type="button" className="btn" onClick={onClose}>
            Cancel
          </button>
        </div>
      </form>
    </Modal>
  );
}

/** The picked account: role, sign-in details, location sharing, and the ... actions. */
function AccountDetail({ u, self, lastSeen, people, tablets, patch, onReset, onResetTwoFactor, onRemove }) {
  const [renaming, setRenaming] = useState(false);
  const [connecting, setConnecting] = useState(false);
  const household = (u.role === 'admin' || u.role === 'family') && !u.disabled;
  return (
    <>
      <section className={'pane' + (u.disabled ? ' is-disabled' : '')} aria-label={u.name}>
        <div className="conn-head">
          <span className={'avatar avatar--lg' + (u.disabled ? '' : ' avatar--on')} aria-hidden="true">
            {initial(u.name)}
          </span>
          <div className="conn-head-text">
            <h2 className="conn-title conn-title--edit">
              {u.name}
              {self ? <span className="muted small"> (you)</span> : null}
              <button type="button" className="icon-btn" aria-label={`Rename ${u.name}`} title="Rename" onClick={() => setRenaming(true)}>
                <Icon name="pencil" size={15} />
              </button>
            </h2>
            <div className="conn-status muted">
              {u.disabled ? "Disabled — can't sign in" : u.role === 'tablet' ? 'Sign in with this account on the tablet' : `Can sign in as ${ROLE_WORD[u.role] || u.role}`}
            </div>
          </div>
          {u.role === 'tablet' ? (
            <button type="button" className="btn btn-primary acct-connect" onClick={() => setConnecting(true)}>
              Connect a device
            </button>
          ) : null}
          <MoreMenu
            items={[
              { label: 'Connect a device…', onClick: () => setConnecting(true) },
              { label: 'Rename…', onClick: () => setRenaming(true) },
              { label: 'Reset password…', onClick: onReset },
              ...(u.twoFactor && u.role !== 'tablet' ? [{ label: 'Turn off two-factor…', onClick: onResetTwoFactor }] : []),
              ...(self ? [] : [{ label: u.disabled ? 'Enable account' : 'Disable account', onClick: () => patch(u, { disabled: !u.disabled }, `${u.name} ${u.disabled ? 'can sign in again' : "can't sign in any more"}`) }]),
              ...(self ? [] : [{ label: 'Remove account…', danger: true, onClick: onRemove }]),
            ]}
            label={`More for ${u.name}`}
          />
        </div>
        <div className="pane-body">
          <dl className="hh-facts">
            <dt>
              <label htmlFor={'role-' + u.id}>Role</label>
            </dt>
            <dd>
              <select id={'role-' + u.id} className="select select--sm hh-fact-select" value={u.role} disabled={self} onChange={(e) => patch(u, { role: e.target.value }, `${u.name} is now ${ROLE_WORD[e.target.value] || e.target.value}`)}>
                <option value="family">Family</option>
                <option value="admin">Admin</option>
                <option value="guest">Guest</option>
                <option value="tablet">Wall tablet</option>
              </select>
              {self ? <span className="muted small hh-fact-note">You can't change your own role.</span> : null}
            </dd>
            <dt>Automations</dt>
            <dd>
              {u.role === 'admin' ? (
                <span>Always (admin)</span>
              ) : u.role === 'tablet' ? (
                <span className="muted">Never (a tablet)</span>
              ) : (
                <>
                  <Switch
                    checked={!!u.canAutomate}
                    label="Make & change automations"
                    onChange={(on) => patch(u, { canAutomate: on }, on ? `${u.name} can make and change automations` : `${u.name} can only view automations`)}
                  />
                  <div className="muted small hh-fact-note">{u.canAutomate ? 'Can create, change, switch off, run and delete them.' : 'View only: sees every automation and when it last ran.'}</div>
                </>
              )}
            </dd>
            {household ? (
              <>
                <dt>
                  <label htmlFor={'loc-' + u.id}>Location</label>
                </dt>
                <dd>
                  <LocationSharing user={u} people={people} tablets={tablets} onPatch={(changes, done) => patch(u, changes, done)} />
                </dd>
              </>
            ) : null}
            {u.role === 'tablet' ? (
              <>
                <dt>People</dt>
                <dd>
                  <Check
                    checked={u.seesPeople !== false}
                    onChange={(on) => patch(u, { seesPeople: on }, on ? `${u.name} can see where people are` : `${u.name} can't see where anyone is`)}
                  >
                    Can see where people are
                  </Check>
                  <div className="muted small hh-fact-note">
                    {u.seesPeople !== false
                      ? 'Everyone sharing by their choice, and those set to Required whose list includes this tablet.'
                      : 'Its People tile shows everyone as not sharing.'}
                  </div>
                </dd>
                <dt>Voice</dt>
                <dd>
                  <VoiceSettings user={u} />
                </dd>
                <dt>Charging</dt>
                <dd>
                  <SmartCharging user={u} />
                </dd>
              </>
            ) : null}
            <dt>Username</dt>
            <dd>{u.username}</dd>
            {u.role !== 'tablet' ? (
              <>
                <dt>Two-factor</dt>
                <dd>{u.twoFactor ? 'On' : 'Off'}</dd>
              </>
            ) : null}
            <dt>Last seen</dt>
            <dd>{lastSeen ? fmtWhen(lastSeen) : u.role === 'tablet' ? 'Not signed in yet' : 'Not signed in anywhere'}</dd>
          </dl>
          <p className="help" style={{ marginTop: 16 }}>
            {u.role === 'guest'
              ? "A guest controls devices but isn't one of the people: never notified, can't see where anyone is."
              : u.role === 'tablet'
                ? 'A wall tablet shows its own dashboard, is never notified and can’t change settings.'
                : "People without an account (under People) still get the notifications they're set up for — they just can't sign in."}
          </p>
        </div>
      </section>
      {renaming ? <RenameAccount user={u} patch={patch} onClose={() => setRenaming(false)} /> : null}
      {connecting ? <ConnectDevice forName={u.role === 'tablet' ? u.name : null} onClose={() => setConnecting(false)} /> : null}
    </>
  );
}

export default function Accounts() {
  const { user: me, isAdmin } = useAuth();
  const confirm = useConfirm();
  const users = useApi(isAdmin ? 'api/users' : null);
  const sessions = useApi(isAdmin ? 'api/auth/sessions?all=1' : null);
  const people = useApi(isAdmin ? 'api/people' : null);
  const [resetting, setResetting] = useState(null);
  const [sel, setSel] = useState(null);
  const reload = () => {
    users.reload();
    sessions.reload();
  };
  const all = users.data || [];
  const selId = sel === ADD ? ADD : sel && all.some((u) => u.id === sel) ? sel : all.some((u) => u.id === me.id) ? me.id : all[0] ? all[0].id : ADD;
  const account = all.find((u) => u.id === selId);
  // Last seen per account, from the sessions list (which names the account).
  const lastSeen = {};
  (sessions.data || []).forEach((s) => {
    if (s.user && (!lastSeen[s.user] || s.lastSeen > lastSeen[s.user])) lastSeen[s.user] = s.lastSeen;
  });

  const patch = async (u, changes, done) => {
    try {
      await api.patch('api/users/' + enc(u.id), changes);
      if (done) toast(done, 'success');
    } catch {
      /* toast shown */
    }
    reload();
  };
  const remove = async (u) => {
    const body =
      u.role === 'tablet'
        ? `${u.name} is signed out and can't sign in again.`
        : `${u.name} can no longer sign in. They stay under People and keep getting the alerts they're set up for.`;
    if (!(await confirm({ title: `Remove ${u.name}'s account?`, body, confirmLabel: 'Remove account', danger: true }))) return;
    try {
      await api.del('api/users/' + enc(u.id));
      toast(`Removed ${u.name}'s account`, 'success');
    } catch {
      /* toast shown */
    }
    reload();
  };
  const resetTwoFactor = async (u) => {
    if (!(await confirm({ title: `Turn off two-factor for ${u.name}?`, body: 'Use this if they lost their phone. They can turn it on again from their profile.', confirmLabel: 'Turn off two-factor', danger: true }))) return;
    patch(u, { resetTwoFactor: true }, `Two-factor turned off for ${u.name}`);
  };

  return (
    <Household current="accounts">
      {!isAdmin ? (
        <div className="card">
          <Empty title="Only an admin can manage accounts" />
        </div>
      ) : users.data === undefined ? (
        users.error ? <ErrorBox error={users.error} onRetry={users.reload} /> : <Loading />
      ) : (
        <HouseholdSplit
          listLabel="Accounts"
          list={
            <div className="rail-list">
              <AddRow label="Add an account" sub="Family, admin, guest or wall tablet" selected={selId === ADD} onClick={() => setSel(ADD)} />
              {users.data.map((u) => (
                <button key={u.id} type="button" className={'rail-row' + (u.disabled ? ' is-disabled' : '')} aria-current={selId === u.id ? 'true' : undefined} onClick={() => setSel(u.id)}>
                  <span className={'avatar' + (u.disabled ? '' : ' avatar--on')} aria-hidden="true">
                    {initial(u.name)}
                  </span>
                  <span className="rail-text">
                    <span className="rail-name">
                      {u.name}
                      {u.id === me.id ? <span className="muted small"> (you)</span> : null}
                    </span>
                    <span className="rail-sub">
                      {ROLE_LABEL[u.role] || u.role} · {u.disabled ? 'disabled' : lastSeen[u.name] ? 'seen ' + fmtWhen(lastSeen[u.name]) : 'not signed in yet'}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          }
        >
          {account ? (
            <AccountDetail
              key={account.id}
              u={account}
              self={account.id === me.id}
              lastSeen={lastSeen[account.name]}
              people={people.data || []}
              tablets={all.filter((x) => x.role === 'tablet' && !x.disabled)}
              patch={patch}
              onReset={() => setResetting(account)}
              onResetTwoFactor={() => resetTwoFactor(account)}
              onRemove={() => remove(account)}
            />
          ) : (
            <AddAccount
              onDone={(u) => {
                reload();
                if (u && u.id) setSel(u.id);
              }}
            />
          )}
        </HouseholdSplit>
      )}
      {resetting ? <ResetPassword user={resetting} onClose={() => setResetting(null)} onDone={reload} /> : null}
    </Household>
  );
}
