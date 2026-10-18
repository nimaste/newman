// Muting a device's problem on the wall (approved "Muted Problems" board): each problem the device
// reports gets Mute -> For 1 day / For 1 week / Until it's fixed / Always (or Cancel). The hub leaves a
// muted problem out of health.issues, so chips, Needs attention and To do drop it by themselves.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { api, enc } from '../api.js';
import { Icon } from '../icons.jsx';

export const MUTE_CHOICES = [
  ['day', 'For 1 day'],
  ['week', 'For 1 week'],
  ['fixed', 'Until it’s fixed'],
  ['always', 'Always'],
];

/** The problems a device reports that nobody muted: [{id, text}]. */
export function unmutedProblems(e) {
  const items = e && e.health && Array.isArray(e.health.items) ? e.health.items : [];
  return items.filter((i) => i && !i.muted);
}

/** "Mute “No AC power”?" with the four choices and Cancel. */
export function MuteChoices({ entity, item, onDone }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState('');
  const pick = async (mode) => {
    setBusy(true);
    setErr('');
    try {
      await api.put(`api/entities/${enc(entity.id)}/mute`, { issue: item.id, for: mode }, { quiet: true });
      onDone();
    } catch (e) {
      setErr((e && e.message) || 'Couldn’t mute it');
      setBusy(false);
    }
  };
  return (
    <div className="wt-mute" role="group" aria-label={`Mute “${item.text}”`}>
      <div className="wt-mute-q">Mute “{item.text}”?</div>
      <div className="wt-mute-sub">{entity.name}. Its other problems still show.</div>
      <div className="wt-mute-choices">
        {MUTE_CHOICES.map(([mode, label]) => (
          <button key={mode} type="button" className="wt-mute-btn" disabled={busy} onClick={() => pick(mode)}>
            {label}
          </button>
        ))}
      </div>
      {err ? (
        <div className="wt-mute-err" role="alert">
          {err}
        </div>
      ) : null}
      <button type="button" className="wt-mute-cancel" disabled={busy} onClick={onDone}>
        Cancel
      </button>
    </div>
  );
}

/** Each unmuted problem of a device with its own Mute button (the choices open in place). */
export function ProblemList({ entity }) {
  const [asking, setAsking] = useState(null);
  const items = unmutedProblems(entity);
  const current = items.find((i) => i.id === asking);
  if (current) return <MuteChoices entity={entity} item={current} onDone={() => setAsking(null)} />;
  if (!items.length) return null;
  return (
    <div className="wt-problems">
      {items.map((i) => (
        <div key={i.id} className="wt-problem">
          <span className="wt-problem-text">{i.text}</span>
          <button type="button" className="wt-mute-open" aria-label={`Mute “${i.text}” on ${entity.name}`} onClick={() => setAsking(i.id)}>
            Mute
          </button>
        </div>
      ))}
    </div>
  );
}

/** A To do row's Mute: the device's problems on a sheet over the wall; it closes once none are left. */
export function MuteSheet({ entity, onClose, host }) {
  const left = unmutedProblems(entity).length;
  useEffect(() => {
    if (!left) onClose();
  }, [left, onClose]);
  useEffect(() => {
    const onKey = (e) => e.key === 'Escape' && onClose();
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);
  const view = (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={`Mute a problem on ${entity.name}`} onClick={(e) => e.target === e.currentTarget && onClose()}>
      <div className="wx-sheet wt-sick-sheet">
        <div className="wt-sick-title">
          <Icon name="alert" size={22} strokeWidth={2.2} />
          <span>{entity.name}</span>
        </div>
        <div className="wx-sheet-body">
          <ProblemList entity={entity} />
        </div>
        <button type="button" className="wx-sheet-close" onClick={onClose}>
          Close
        </button>
      </div>
    </div>
  );
  return host ? createPortal(view, host) : view;
}
