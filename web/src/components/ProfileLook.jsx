// How a person looks on the family maps: their colour (preset swatches) and photo (upload / replace /
// remove). Their own (Profile: /api/me/...) or, for an admin, anyone's (Household -> People).

import { useRef, useState } from 'react';
import { enc, toast, api } from '../api.js';
import { Avatar } from './FamilyBits.jsx';
import { colorOf } from './family.js';

// The hub's palette (profiles.py), plus a few more.
export const SWATCHES = ['#E5734A', '#3F8EDB', '#3FAE7A', '#9B6BD6', '#E2A93B', '#D9578A', '#2FA5A8', '#7C8CE0',
  '#B5794A', '#6E9E3C', '#F3B941', '#5B6478'];

export default function ProfileLook({ person, mine, onChanged }) {
  const file = useRef(null);
  const [busy, setBusy] = useState(false);
  const base = mine ? 'api/me' : 'api/people/' + enc(person.id);
  const current = colorOf(person).toUpperCase();

  const setColor = async (color) => {
    try {
      await api.put((mine ? 'api/me' : base) + '/profile', { color });
      onChanged && onChanged();
    } catch {
      /* toast shown */
    }
  };
  const upload = async (f) => {
    if (!f) return;
    if (f.size > 3 * 1024 * 1024) {
      toast('That picture is over 3 MB - pick a smaller one', 'error');
      return;
    }
    setBusy(true);
    try {
      const r = await fetch(base + '/photo', { method: 'PUT', credentials: 'same-origin', headers: { 'Content-Type': f.type || 'image/jpeg' }, body: f });
      const j = await r.json().catch(() => ({}));
      if (!r.ok) toast(j.error || "Couldn't save the photo", 'error');
      else toast('Photo saved', 'success');
    } catch {
      toast("Can't reach the Newman hub.", 'error');
    } finally {
      setBusy(false);
      if (file.current) file.current.value = '';
      onChanged && onChanged();
    }
  };
  const remove = async () => {
    try {
      await api.del(base + '/photo');
      onChanged && onChanged();
    } catch {
      /* toast shown */
    }
  };

  return (
    <div className="fam-look stack-sm">
      <h2 className="group-title">On the family map</h2>
      <div className="fam-look-row">
        <Avatar person={person} size={64} />
        <div className="stack-sm grow">
          <div className="fam-look-label">Colour</div>
          <div className="fam-swatches" role="radiogroup" aria-label="Colour">
            {SWATCHES.map((c) => (
              <button
                key={c}
                type="button"
                role="radio"
                aria-checked={current === c}
                aria-label={c}
                className={'fam-swatch' + (current === c ? ' is-on' : '')}
                style={{ background: c }}
                onClick={() => setColor(c)}
              />
            ))}
            {!SWATCHES.includes(current) ? <span className="fam-swatch is-on" style={{ background: current }} title="Current colour" /> : null}
          </div>
          <div className="fam-look-label">Photo</div>
          <div className="fam-look-actions">
            <input ref={file} type="file" accept="image/jpeg,image/png,image/webp" className="sr-only" onChange={(e) => upload(e.target.files && e.target.files[0])} />
            <button type="button" className="btn btn-sm" disabled={busy} onClick={() => file.current && file.current.click()}>
              {busy ? 'Saving…' : person.photo ? 'Replace photo' : 'Upload a photo'}
            </button>
            {person.photo ? (
              <button type="button" className="btn btn-sm btn-ghost" disabled={busy} onClick={remove}>
                Remove photo
              </button>
            ) : null}
            <span className="muted small">JPEG, PNG or WebP, up to 3 MB - cropped to a square.</span>
          </div>
        </div>
      </div>
    </div>
  );
}
