// Device picker as a small popover: a search box on top, device types on the
// left, that type's devices on the right. Used for the blanks in the
// automation builder rows ("When [Side Door] or [Laundry] …").

import { useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useHub } from '../hooks.js';
import { Icon } from '../icons.jsx';
import { entityStateText } from '../model.js';

const W = 580;
const H = 430;

function Popover({ anchor, include, selected = [], multi, onPick, onClose, exclude = [] }) {
  const { entities, kinds, kindsById } = useHub();
  const box = useRef(null);
  const search = useRef(null);
  const [q, setQ] = useState('');
  const [chosen, setChosen] = useState([]);
  const [pos, setPos] = useState(null);
  const all = (entities || []).filter((e) => include(e, kindsById[e.kind]) && !exclude.includes(e.id));
  const types = kinds.map((k) => ({ k, items: all.filter((e) => e.kind === k.id) })).filter((t) => t.items.length);
  const firstSel = all.find((e) => selected.includes(e.id));
  const [type, setType] = useState(firstSel ? firstSel.kind : types[0] ? types[0].k.id : '');
  const needle = q.trim().toLowerCase();
  const shown = needle
    ? all.filter((e) => e.name.toLowerCase().includes(needle) || ((e.attrs && e.attrs.area) || '').toLowerCase().includes(needle))
    : all.filter((e) => e.kind === type);

  // Fixed position next to the blank that opened it, kept inside the window.
  useLayoutEffect(() => {
    const r = anchor.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const width = Math.min(W, vw - 32);
    const left = Math.max(16, Math.min(r.left, vw - width - 16));
    let top = r.bottom + 6;
    if (top + H > vh - 12) top = Math.max(12, r.top - 6 - H);
    setPos({ left, top, width });
  }, [anchor]);

  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    search.current && search.current.focus();
    const onDoc = (e) => {
      if (box.current && !box.current.contains(e.target) && !anchor.contains(e.target)) closeRef.current();
    };
    const onKey = (e) => {
      if (e.key === 'Escape') {
        e.stopPropagation();
        closeRef.current();
        anchor.focus();
      }
    };
    document.addEventListener('mousedown', onDoc);
    window.addEventListener('keydown', onKey, true);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      window.removeEventListener('keydown', onKey, true);
    };
  }, [anchor]);

  const pick = (id) => {
    if (!multi) {
      onPick([id]);
      onClose();
      anchor.focus();
      return;
    }
    setChosen((c) => (c.includes(id) ? c.filter((x) => x !== id) : [...c, id]));
  };

  return (
    <div
      ref={box}
      className="dpop"
      role="dialog"
      aria-label={multi ? 'Choose devices' : 'Choose a device'}
      style={pos ? { left: pos.left, top: pos.top, width: pos.width } : { visibility: 'hidden' }}
    >
      <input
        ref={search}
        className="input input--sm2 dpop-search"
        type="search"
        placeholder={`Type to search ${all.length} devices…`}
        value={q}
        onChange={(e) => setQ(e.target.value)}
        aria-label="Search devices"
      />
      <div className="dpop-cols">
        <div className="dpop-types" role="tablist" aria-label="Device types">
          {types.map((t) => (
            <button
              key={t.k.id}
              type="button"
              role="tab"
              aria-selected={!needle && type === t.k.id}
              className="dpop-type"
              onClick={() => {
                setQ('');
                setType(t.k.id);
              }}
            >
              <span>{t.k.plural || t.k.name}</span>
              <span className="dpop-count">{t.items.length}</span>
            </button>
          ))}
        </div>
        <div className="dpop-list" role="listbox" aria-multiselectable={multi || undefined}>
          {!shown.length ? <div className="muted dpop-empty">Nothing matches.</div> : null}
          {shown.map((e) => {
            const on = multi ? chosen.includes(e.id) : selected.includes(e.id);
            const room = e.attrs && e.attrs.area && !e.name.includes(e.attrs.area) ? e.attrs.area : null;
            return (
              <button key={e.id} type="button" role="option" aria-selected={on} className="dpop-item" onClick={() => pick(e.id)}>
                {multi ? (
                  <span className="box" aria-hidden="true" style={on ? { background: 'var(--teal)', borderColor: 'var(--teal)' } : undefined}>
                    {on ? <Icon name="check" size={13} strokeWidth={3} /> : null}
                  </span>
                ) : null}
                <span className="dpop-name">
                  {e.name}
                  {room ? <span className="muted"> · {room}</span> : null}
                  {needle && kindsById[e.kind] ? <span className="muted"> · {kindsById[e.kind].name}</span> : null}
                </span>
                <span className="dpop-state">{entityStateText(e, kindsById[e.kind])}</span>
              </button>
            );
          })}
        </div>
      </div>
      {multi ? (
        <div className="dpop-foot">
          <span className="muted">{chosen.length ? `${chosen.length} chosen` : 'Tick one or more'}</span>
          <button
            type="button"
            className="btn btn-primary btn-sm2"
            disabled={!chosen.length}
            onClick={() => {
              onPick(chosen);
              onClose();
              anchor.focus();
            }}
          >
            Add {chosen.length > 1 ? chosen.length + ' devices' : 'device'}
          </button>
        </div>
      ) : null}
    </div>
  );
}

/** A blank showing one chosen device; click to swap it, × to remove it. */
export function DeviceBlank({ id, include, onChange, onRemove, placeholder = 'Choose a device', exclude = [] }) {
  const { entitiesById } = useHub();
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  const e = id ? entitiesById[id] : null;
  return (
    <span className="blank-wrap">
      <button ref={ref} type="button" className={'blank' + (id ? '' : ' blank--empty')} aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <span className="blank-text">{e ? e.name : id ? 'Missing device' : placeholder}</span>
        <span className="blank-caret" aria-hidden="true">
          ▾
        </span>
      </button>
      {onRemove && id ? (
        <button type="button" className="blank-x" aria-label={`Remove ${e ? e.name : 'device'}`} onClick={onRemove}>
          <Icon name="x" size={12} strokeWidth={2.6} />
        </button>
      ) : null}
      {open ? (
        <Popover anchor={ref.current} include={include} selected={id ? [id] : []} exclude={exclude} onPick={(ids) => onChange(ids[0])} onClose={() => setOpen(false)} />
      ) : null}
    </span>
  );
}

/** "+ Add device": opens the popover in multi-select mode. */
export function AddDeviceButton({ include, exclude = [], onAdd, label = 'Add device' }) {
  const ref = useRef(null);
  const [open, setOpen] = useState(false);
  return (
    <span className="blank-wrap">
      <button ref={ref} type="button" className="add-dev" aria-haspopup="dialog" aria-expanded={open} onClick={() => setOpen((o) => !o)}>
        <Icon name="plus" size={14} /> {label}
      </button>
      {open ? <Popover anchor={ref.current} include={include} exclude={exclude} multi onPick={onAdd} onClose={() => setOpen(false)} /> : null}
    </span>
  );
}

/**
 * A list of device blanks joined by a word ("or" / "and") plus "+ Add device".
 * `swapInclude` (optional) filters what a blank can be swapped to, e.g. any
 * kind for the only device of a row while "+ Add device" stays on that kind.
 */
export function DeviceBlanks({ ids, onChange, include, swapInclude, joiner = 'or', single = false, emptyLabel }) {
  if (!ids.length) {
    return (
      <AddDeviceButton
        include={include}
        label={emptyLabel || (single ? 'Choose a device' : 'Choose devices')}
        onAdd={(add) => onChange(single ? add.slice(0, 1) : add)}
      />
    );
  }
  return (
    <>
      {ids.map((id, i) => (
        <span key={id + i} className="blank-seq">
          {i ? <span className="joiner">{joiner}</span> : null}
          <DeviceBlank
            id={id}
            include={swapInclude || include}
            exclude={ids.filter((x) => x !== id)}
            onChange={(nid) => onChange(ids.map((x, j) => (j === i ? nid : x)))}
            onRemove={ids.length > 1 || !single ? () => onChange(ids.filter((_, j) => j !== i)) : null}
          />
        </span>
      ))}
      {!single ? <AddDeviceButton include={include} exclude={ids} onAdd={(add) => onChange([...ids, ...add.filter((x) => !ids.includes(x))])} /> : null}
    </>
  );
}
