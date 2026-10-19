// Light groups (admins): lights or switches that act as one - one switch and one brightness on the phones,
// the wall and in automations (GET/POST api/groups, PUT/DELETE api/groups/{id}). A device can be in one
// group; a group has a room (its members take it).

import { useMemo, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useConfirm } from './Feedback.jsx';
import { Check, Empty, ErrorBox, Loading, Modal } from './Controls.jsx';
import { RoomSelect } from './RoomPicker.jsx';
import { roomOf } from '../model.js';
import { plural } from '../format.js';

/** What the hub lets into a group: something in the lighting category that turns on and off (kinds metadata). */
export function groupable(e, kindsById) {
  const k = e && kindsById[e.kind];
  if (!k || e.integration === 'groups' || k.category !== 'lighting') return false;
  const ids = new Set((k.commands || []).map((c) => c.id));
  return ids.has('turn_on') && ids.has('turn_off');
}

function GroupForm({ group, groups, onDone, onCancel }) {
  const { entities, kindsById } = useHub();
  const [name, setName] = useState(group ? group.name : '');
  const [room, setRoom] = useState(group ? group.room : null);
  const [members, setMembers] = useState(() => new Set(group ? group.members : []));
  const [search, setSearch] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');
  const inOther = useMemo(() => {
    const m = {};
    for (const g of groups) if (!group || g.id !== group.id) for (const id of g.members) m[id] = g.name;
    return m;
  }, [groups, group]);
  const needle = search.trim().toLowerCase();
  const choices = (entities || [])
    .filter((e) => groupable(e, kindsById))
    .filter((e) => !needle || e.name.toLowerCase().includes(needle) || roomOf(e).toLowerCase().includes(needle))
    .sort((a, b) => (roomOf(a) || '~').localeCompare(roomOf(b) || '~') || a.name.localeCompare(b.name));
  const toggle = (id) =>
    setMembers((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  const save = async () => {
    setSaving(true);
    setError('');
    try {
      const body = { name: name.trim(), members: [...members], room: room || null };
      const out = group ? await api.put('api/groups/' + enc(group.id), body, { quiet: true }) : await api.post('api/groups', body, { quiet: true });
      toast(group ? `Saved ${out.name}` : `Made the group ${out.name}`, 'success');
      onDone();
    } catch (e) {
      setError(e.message || 'That didn’t save');
    } finally {
      setSaving(false);
    }
  };
  return (
    <div className="stack">
      <label className="field">
        <span className="field-label">Name</span>
        <input className="input" maxLength={64} value={name} onChange={(e) => setName(e.target.value)} placeholder="Kitchen Under Cabinet" autoFocus />
      </label>
      <label className="field">
        <span className="field-label">Room</span>
        <RoomSelect value={room} onChange={setRoom} />
      </label>
      <div className="field">
        <span className="field-label">Lights and switches ({members.size})</span>
        <input className="input input--sm" placeholder="Find a light" value={search} onChange={(e) => setSearch(e.target.value)} aria-label="Find a light" />
        <div className="group-pick" role="group" aria-label="Lights and switches">
          {choices.map((e) => (
            <div key={e.id} className="group-pick-row">
              <Check checked={members.has(e.id)} disabled={!!inOther[e.id]} onChange={() => toggle(e.id)}>
                {e.name}
                <span className="muted group-pick-meta">{inOther[e.id] ? `In ${inOther[e.id]}` : roomOf(e)}</span>
              </Check>
            </div>
          ))}
          {!choices.length ? <div className="muted">No lights or switches match.</div> : null}
        </div>
      </div>
      {error ? <div className="error-box" role="alert">{error}</div> : null}
      <div className="group-actions">
        <button type="button" className="btn" onClick={onCancel}>
          Cancel
        </button>
        <button type="button" className="btn btn-primary" disabled={saving || !name.trim() || !members.size} onClick={save}>
          {saving ? 'Saving…' : group ? 'Save' : 'Make the group'}
        </button>
      </div>
    </div>
  );
}

export default function LightGroupsDialog({ onClose }) {
  const res = useApi('api/groups');
  const { entitiesById } = useHub();
  const confirm = useConfirm();
  const [editing, setEditing] = useState(null); // 'new' | a group
  const groups = res.data || [];
  const remove = async (g) => {
    const ok = await confirm({ title: `Remove the group ${g.name}?`, body: 'Its lights stay; they just aren’t one any more. Automations that used the group show it as missing.', confirmLabel: 'Remove', danger: true });
    if (!ok) return;
    try {
      await api.del('api/groups/' + enc(g.id));
      toast(`Removed ${g.name}`, 'success');
      res.reload();
    } catch {
      /* toast shown */
    }
  };
  const done = () => {
    setEditing(null);
    res.reload();
  };
  const title = editing === 'new' ? 'New light group' : editing ? `Edit ${editing.name}` : 'Light groups';
  return (
    <Modal title={title} onClose={onClose} wide>
      {editing ? (
        <GroupForm group={editing === 'new' ? null : editing} groups={groups} onDone={done} onCancel={() => setEditing(null)} />
      ) : res.data === undefined ? (
        res.error ? <ErrorBox error={res.error} onRetry={res.reload} /> : <Loading />
      ) : (
        <div className="stack">
          <p className="help">Lights or switches that act as one: one switch and one brightness on the phones, the wall tablet and in automations.</p>
          {groups.length ? (
            <div className="rows">
              {groups.map((g) => (
                <div key={g.id} className="row">
                  <div style={{ minWidth: 0 }}>
                    <div className="strong">{g.name}</div>
                    <div className="muted" style={{ fontSize: 13 }}>
                      {[g.room, plural(g.members.length, g.kind === 'switch' ? 'switch' : 'light', g.kind === 'switch' ? 'switches' : 'lights'), g.members.map((m) => (entitiesById[m] || {}).name || m).join(', ')].filter(Boolean).join(' · ')}
                    </div>
                  </div>
                  <span className="dtable-actions">
                    <button type="button" className="btn btn-sm" onClick={() => setEditing(g)}>
                      Edit
                    </button>
                    <button type="button" className="btn btn-sm btn-danger" onClick={() => remove(g)}>
                      Remove
                    </button>
                  </span>
                </div>
              ))}
            </div>
          ) : (
            <Empty title="No groups yet">Put lights that belong together in one group, like three under-cabinet drivers on one switch.</Empty>
          )}
          <div>
            <button type="button" className="btn btn-primary" onClick={() => setEditing('new')}>
              New group
            </button>
          </div>
        </div>
      )}
    </Modal>
  );
}
