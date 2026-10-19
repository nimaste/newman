// A device's category and icon (admins, on its page): where it shows up (its Home card, Devices, the
// category screens) and the icon it's drawn with. Its controls stay its kind's. PUT
// api/entities/{id}/category {category | null, newName?} and api/entities/{id}/icon {icon | null}.

import { useState } from 'react';
import { api, enc } from '../api.js';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { categoryOf, kindIcon } from '../model.js';

const NEW = '\u0000new';
const TYPE = '\u0000type';

const words = (name) => String(name).replace(/[_-]+/g, ' ').replace(/^./, (c) => c.toUpperCase());

export default function CategoryIcon({ entity: e }) {
  const { categories, categoriesById, kindsById, meta } = useHub();
  const kind = kindsById[e.kind];
  const typeCat = kind ? kind.category : null;
  const current = categoryOf(e, kindsById);
  const moved = !!(typeCat && current && current !== typeCat);
  const [adding, setAdding] = useState(false);
  const [text, setText] = useState('');
  const [busy, setBusy] = useState(false);
  const nameOf = (id) => (categoriesById[id] && categoriesById[id].name) || words(id || '');

  const setCategory = async (body) => {
    setBusy(true);
    try {
      await api.put('api/entities/' + enc(e.id) + '/category', body);
      setAdding(false);
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };
  const setIcon = async (icon) => {
    setBusy(true);
    try {
      await api.put('api/entities/' + enc(e.id) + '/icon', { icon });
    } catch {
      /* toast shown */
    } finally {
      setBusy(false);
    }
  };

  const icons = (meta && meta.icons) || [];
  return (
    <div className="dev-cat" aria-label="Category and icon">
      <label className="dev-cat-field">
        <span className="dev-cat-label">Category</span>
        {adding ? (
          <span className="room-new">
            <input
              className="input input--sm"
              autoFocus
              maxLength={40}
              placeholder="New category"
              aria-label="New category name"
              value={text}
              onChange={(ev) => setText(ev.target.value)}
              onKeyDown={(ev) => {
                if (ev.key === 'Enter' && text.trim()) {
                  ev.preventDefault();
                  setCategory({ newName: text.trim() });
                }
                if (ev.key === 'Escape') setAdding(false);
              }}
            />
            <button type="button" className="btn btn-sm" disabled={!text.trim() || busy} onClick={() => setCategory({ newName: text.trim() })}>
              Add
            </button>
            <button type="button" className="btn-link" onClick={() => setAdding(false)}>
              Cancel
            </button>
          </span>
        ) : (
          <select
            className="input input--sm"
            value={current || ''}
            disabled={busy}
            onChange={(ev) => {
              const v = ev.target.value;
              if (v === NEW) {
                setText('');
                setAdding(true);
              } else if (v === TYPE || v === typeCat) setCategory({ category: null });
              else setCategory({ category: v });
            }}
          >
            {categories.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
                {c.id === typeCat ? ' (from its type)' : ''}
              </option>
            ))}
            {current && !categoriesById[current] ? <option value={current}>{nameOf(current)}</option> : null}
            <option value={NEW}>New category…</option>
            {moved ? <option value={TYPE}>Back to its type's category ({nameOf(typeCat)})</option> : null}
          </select>
        )}
      </label>
      <label className="dev-cat-field">
        <span className="dev-cat-label">Icon</span>
        <span className="dev-cat-icon" aria-hidden="true">
          <Icon name={e.icon || kindIcon(kind)} size={18} />
        </span>
        <select className="input input--sm" value={e.icon || ''} disabled={busy} onChange={(ev) => setIcon(ev.target.value || null)}>
          <option value="">{e.icon ? "Back to its type's icon" : "Its type's icon"}</option>
          {icons.map((n) => (
            <option key={n} value={n}>
              {words(n)}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
