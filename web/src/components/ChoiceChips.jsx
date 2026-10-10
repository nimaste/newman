// Values of commands with valueType "choices": pick any of the device's items (attrs[cmd.attr],
// each {id, name}; e.g. a vacuum's rooms). The value sent is the list of chosen ids.

import { useState } from 'react';
import { Modal } from './Controls.jsx';
import { Icon } from '../icons.jsx';
import { choiceItems } from '../model.js';

/** A row of toggle chips; `value` is the list of chosen ids. */
export function ChoiceChips({ items, value, onChange, label, className = '' }) {
  const chosen = Array.isArray(value) ? value.map(String) : [];
  const toggle = (id) => {
    const k = String(id);
    const next = chosen.includes(k) ? chosen.filter((x) => x !== k) : [...chosen, k];
    // Keep the ids' own type (numbers stay numbers), in the device's order.
    onChange(items.filter((x) => next.includes(String(x.id))).map((x) => x.id));
  };
  if (!items.length) return <span className="muted small">Nothing to choose from yet.</span>;
  return (
    <span className={'choice-chips ' + className} role="group" aria-label={label}>
      {items.map((x) => {
        const on = chosen.includes(String(x.id));
        return (
          <button key={x.id} type="button" className={'choice-chip' + (on ? ' is-on' : '')} aria-pressed={on} onClick={() => toggle(x.id)}>
            {on ? <Icon name="check" size={13} strokeWidth={2.6} /> : null}
            {x.name}
          </button>
        );
      })}
    </span>
  );
}

/** "Clean rooms…": a button that opens the chips in a small dialog, then Go. */
export function ChoicesButton({ entity, cmd, disabled, busy, onGo, size = 'sm' }) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState([]);
  const items = choiceItems(entity, cmd);
  return (
    <>
      <button
        type="button"
        className={'btn ' + (size === 'lg' ? '' : 'btn-sm')}
        disabled={disabled || !items.length}
        title={items.length ? undefined : `${entity.name} hasn't listed any yet`}
        onClick={() => {
          setPicked([]);
          setOpen(true);
        }}
        aria-label={`${cmd.label} ${entity.name}…`}
      >
        {busy ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name={cmd.icon || 'list'} size={14} />}
        {cmd.label}…
      </button>
      {open ? (
        <Modal title={`${cmd.label}: ${entity.name}`} onClose={() => setOpen(false)}>
          <div className="stack-sm">
            <div className="help">Pick one or more, then Go.</div>
            <ChoiceChips items={items} value={picked} onChange={setPicked} label={cmd.label} className="choice-chips--lg" />
            <div className="inline-form">
              <button
                type="button"
                className="btn btn-primary"
                disabled={!picked.length}
                onClick={() => {
                  setOpen(false);
                  onGo(picked);
                }}
              >
                Go{picked.length ? ` (${picked.length})` : ''}
              </button>
              <button type="button" className="btn" onClick={() => setOpen(false)}>
                Cancel
              </button>
            </div>
          </div>
        </Modal>
      ) : null}
    </>
  );
}
