// Removing offline devices from the hub (POST /api/entities/remove): the in-page confirmation, which
// first asks the hub what still uses them (automations, dashboards, phone layouts, widgets).

import { useEffect, useState } from 'react';
import { api } from '../api.js';
import { ErrorBox, Loading, Modal } from './Controls.jsx';
import { Icon } from '../icons.jsx';
import { plural } from '../format.js';

const USED_AS = [
  ['automation', 'automation'],
  ['dashboard', 'dashboard'],
  ['phoneLayout', 'phone layout'],
  ['widget', 'phone widget'],
];

/** "Used by 2 automations: Night lock, Away; 1 dashboard: Garden". */
export function usedByText(usedBy) {
  const parts = USED_AS.map(([type, word]) => {
    const mine = (usedBy || []).filter((u) => u.type === type);
    if (!mine.length) return null;
    if (type === 'widget') return plural(mine.length, word);
    return `${plural(mine.length, word)}: ${mine.map((u) => u.name).join(', ')}`;
  }).filter(Boolean);
  return parts.length ? 'Used by ' + parts.join('; ') : '';
}

/** Is the device offline as the screens show it ("Offline")? Only those can be removed. */
export function isOffline(e) {
  return !!e && (e.available === false || e.state === 'offline');
}

/**
 * The confirmation. `devices`: the entities to remove; onDone(removedIds) once removed; onClose to cancel.
 */
export default function RemoveDevicesDialog({ devices, onClose, onDone }) {
  const ids = devices.map((e) => e.id);
  const key = ids.join('\n');
  const [check, setCheck] = useState(null); // {devices: [{id, name, usedBy}]} from the dry run
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let live = true;
    setCheck(null);
    setError(null);
    api
      .post('api/entities/remove', { ids: key.split('\n'), dryRun: true }, { quiet: true })
      .then((r) => live && setCheck(r))
      .catch((err) => live && setError(err));
    return () => {
      live = false;
    };
  }, [key]);

  const rows = check ? check.devices : [];
  const used = rows.filter((d) => d.usedBy && d.usedBy.length);
  const what = ids.length === 1 ? devices[0].name : plural(ids.length, 'device');

  const remove = async () => {
    setBusy(true);
    try {
      const r = await api.post('api/entities/remove', { ids, force: true }, { quiet: true });
      onDone(r.removed || ids);
    } catch (err) {
      setError(err);
      setBusy(false);
    }
  };

  return (
    <Modal
      title={ids.length === 1 ? `Remove ${devices[0].name}?` : `Remove ${plural(ids.length, 'device')}?`}
      onClose={busy ? () => {} : onClose}
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-danger-solid" onClick={remove} disabled={busy || !check}>
            {busy ? 'Removing…' : used.length ? `Remove anyway` : `Remove ${what}`}
          </button>
        </>
      }
    >
      <div className="stack">
        <p style={{ margin: 0 }}>
          {ids.length === 1 ? 'It goes' : 'They go'} from Newman along with any name or settings given here. If
          {ids.length === 1 ? ' its' : ' their'} connection reports {ids.length === 1 ? 'it' : 'one'} again later, it comes back as a
          new device.
        </p>
        {error ? <ErrorBox error={error} /> : null}
        {!check && !error ? <Loading>Checking what uses {ids.length === 1 ? 'it' : 'them'}…</Loading> : null}
        {used.length ? (
          <div className="warn" role="note">
            <Icon name="alert" size={16} />
            <span>
              {used.length === 1 && ids.length === 1 ? 'Still in use' : `${plural(used.length, 'device')} still in use`}. Removing{' '}
              {used.length === 1 ? 'it' : 'them'} leaves what uses {used.length === 1 ? 'it' : 'them'} with a missing device.
            </span>
          </div>
        ) : null}
        {check && ids.length === 1 && used.length ? <div className="amber">{usedByText(used[0].usedBy)}</div> : null}
        {check && ids.length > 1 ? (
          <ul className="remove-list" aria-label="Devices to remove">
            {rows.map((d) => (
              <li key={d.id}>
                <span className="strong">{d.name}</span>
                {d.usedBy && d.usedBy.length ? <span className="amber"> · {usedByText(d.usedBy)}</span> : null}
              </li>
            ))}
          </ul>
        ) : null}
      </div>
    </Modal>
  );
}
