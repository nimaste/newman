import { useState } from 'react';
import { useAuth } from '../auth.jsx';
import { Icon } from '../icons.jsx';

/** Hash routes of the Household page (one hub = one household). */
export const HOUSEHOLD = '#/household';

/** The Household page's frame, the same on every sub-tab: the title and the tabs in a fixed
 *  header, then a fixed-width list column and a panel filling the rest. Sub-tabs put their
 *  actions in the list ("+ Add ..." rows) or the panel, never in the header, so switching
 *  tabs moves nothing. */
export default function Household({ current, children }) {
  const { user, isAdmin } = useAuth();
  const household = !!user && (user.role === 'admin' || user.role === 'family');
  const tabs = [
    { id: 'people', label: 'People', href: HOUSEHOLD },
    ...(household ? [{ id: 'map', label: 'Map', href: HOUSEHOLD + '/map' }] : []),
    { id: 'phones', label: 'Phones', href: HOUSEHOLD + '/phones' },
    ...(isAdmin ? [{ id: 'places', label: 'Places', href: HOUSEHOLD + '/places' }] : []),
    ...(isAdmin ? [{ id: 'accounts', label: 'Accounts', href: HOUSEHOLD + '/accounts' }] : []),
  ];
  return (
    <div className="app-page hh-page">
      <div className="app-head hh-head">
        <h1 className="page-title">Household</h1>
        <nav className="tabs hh-tabs" aria-label="Household sections">
          {tabs.map((t) => (
            <a key={t.id} href={t.href} className="tab" aria-current={current === t.id ? 'page' : undefined}>
              {t.label}
            </a>
          ))}
        </nav>
      </div>
      {children}
    </div>
  );
}

/** The two columns every sub-tab uses: `list` on the left (fixed width), `children` on the right. */
export function HouseholdSplit({ list, listLabel, children }) {
  return (
    <div className="hh-split">
      <nav className="rail pane hh-list" aria-label={listLabel}>
        {list}
      </nav>
      {children}
    </div>
  );
}

/** "+ Add ..." as the first row of a list. Selecting it opens the add form in the panel. */
export function AddRow({ label, sub, selected, onClick }) {
  return (
    <button type="button" className="rail-row rail-row--add" aria-current={selected ? 'true' : undefined} onClick={onClick}>
      <span className="phone-icon" aria-hidden="true">
        <Icon name="plus" size={16} />
      </span>
      <span className="rail-text">
        <span className="rail-name">{label}</span>
        {sub ? <span className="rail-sub">{sub}</span> : null}
      </span>
    </button>
  );
}

/** "+ Add ..." that turns into a one-line name entry in place (same height, nothing moves). */
export function AddNameRow({ label, sub, placeholder, busy, onAdd }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  if (!open) return <AddRow label={label} sub={sub} onClick={() => setOpen(true)} />;
  const submit = async (e) => {
    e.preventDefault();
    const v = name.trim();
    if (!v) return;
    if ((await onAdd(v)) !== false) {
      setName('');
      setOpen(false);
    }
  };
  return (
    <form className="rail-row rail-row--add rail-row--form" onSubmit={submit}>
      <input
        className="input input--sm"
        aria-label={label}
        placeholder={placeholder}
        value={name}
        autoFocus
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === 'Escape') {
            setName('');
            setOpen(false);
          }
        }}
        onBlur={(e) => {
          // Leaving an empty entry closes it; a typed name stays until added or Escape.
          if (!name.trim() && !(e.relatedTarget && e.currentTarget.form.contains(e.relatedTarget))) setOpen(false);
        }}
      />
      <button type="submit" className="btn btn-primary btn-sm" disabled={busy || !name.trim()}>
        Add
      </button>
    </form>
  );
}
