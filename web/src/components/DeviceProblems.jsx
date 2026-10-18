// A device's problems on its page (approved "Muted Problems" board): each with Mute ▾ -> 1 day / 1 week /
// Until it's fixed / Always, and the muted ones below in grey with Unmute. The hub keeps the mutes
// (PUT/DELETE api/entities/{id}/mute) and leaves muted problems out of health.issues everywhere else.

import { useState } from 'react';
import { api, enc } from '../api.js';
import { Icon } from '../icons.jsx';

const CHOICES = [
  ['day', '1 day'],
  ['week', '1 week'],
  ['fixed', 'Until it’s fixed'],
  ['always', 'Always'],
];

function mutedText(m) {
  if (m.mode === 'always') return 'Muted always';
  if (m.mode === 'fixed') return 'Muted until it’s fixed';
  if (typeof m.until === 'number') {
    const d = new Date(m.until * 1000);
    const when = d.toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
    return `Muted until ${when}`;
  }
  return 'Muted';
}

export default function DeviceProblems({ entity }) {
  const [open, setOpen] = useState(null);
  const [busy, setBusy] = useState(false);
  const h = entity.health || {};
  // An older hub sends only the words: show them, nothing to mute.
  const items = Array.isArray(h.items) ? h.items : (h.issues || []).map((text) => ({ id: null, text, muted: null }));
  const active = items.filter((i) => !i.muted);
  const muted = items.filter((i) => i.muted);
  if (!items.length) return null;
  const path = `api/entities/${enc(entity.id)}/mute`;
  const mute = async (item, mode) => {
    setBusy(true);
    try {
      await api.put(path, { issue: item.id, for: mode });
      setOpen(null);
    } catch {
      /* the toast says why */
    } finally {
      setBusy(false);
    }
  };
  const unmute = async (item) => {
    setBusy(true);
    try {
      await api.del(`${path}/${enc(item.id)}`);
    } catch {
      /* the toast says why */
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="dev-problems">
      {active.length ? (
        <div className="warn dev-problem-list" role="note">
          {active.map((i) => (
            <div key={i.id || i.text} className="dev-problem">
              <div className="dev-problem-row">
                <Icon name="alert" size={16} />
                <span className="dev-problem-text">{i.text}</span>
                {i.id ? (
                  <button type="button" className="btn btn-sm2 dev-problem-mute" aria-expanded={open === i.id} onClick={() => setOpen(open === i.id ? null : i.id)}>
                    Mute ▾
                  </button>
                ) : null}
              </div>
              {open === i.id ? (
                <div className="dev-problem-choices" role="group" aria-label={`Mute “${i.text}” for how long`}>
                  {CHOICES.map(([mode, label]) => (
                    <button key={mode} type="button" className="btn btn-sm2" disabled={busy} onClick={() => mute(i, mode)}>
                      {label}
                    </button>
                  ))}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : (
        <div className="dev-problems-ok">Nothing needs attention</div>
      )}
      {muted.length ? (
        <div className="dev-muted">
          <div className="eyebrow eyebrow--sm">Muted</div>
          {muted.map((i) => (
            <div key={i.id} className="dev-muted-row">
              <span className="dev-muted-text">
                <span className="dev-muted-name">{i.text}</span>
                <span className="dev-muted-until">
                  {mutedText(i.muted)}
                  {i.muted.by ? ` · by ${i.muted.by}` : ''}
                </span>
              </span>
              <button type="button" className="btn btn-sm2" disabled={busy} onClick={() => unmute(i)}>
                Unmute
              </button>
            </div>
          ))}
        </div>
      ) : null}
    </div>
  );
}
