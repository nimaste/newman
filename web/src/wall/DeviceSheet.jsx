// A device's other actions on the wall (a vacuum's Send home, Find, Clean rooms…), in a small
// glass sheet. Everything comes from the kind's commands: plain ones are buttons, "choices"
// ones (valueType) open chips of the device's items (attrs[cmd.attr]) and then Go.

import { useEffect, useState } from 'react';
import { createPortal } from 'react-dom';
import { Icon } from '../icons.jsx';
import { choiceCommands, choiceItems, entityStateText, plainCommands, toggleDuo } from '../model.js';
import { useWallCommand } from './PinPad.jsx';

/** Actions beyond the tile's main tap: only for devices with more than an on/off pair. */
export function extraActions(e, kind, main) {
  const plain = plainCommands(kind);
  const choices = choiceCommands(kind);
  if (plain.length <= 2 && !choices.length) return [];
  const duo = toggleDuo(kind, plain);
  return [...plain.filter((c) => c !== main && (!duo || (c !== duo.on && c !== duo.off))), ...choices];
}

/** Battery % and a reported problem, from the device's properties (any kind). */
export function deviceVitals(e) {
  const props = Array.isArray(e.properties) ? e.properties : [];
  const battery = props.find((p) => p.id === 'battery' && typeof p.value === 'number');
  const problem = props.find((p) => p.id === 'problem' && p.value);
  return { battery: battery ? Math.round(battery.value) : null, problem: problem ? String(problem.value) : null };
}

function Chips({ e, cmd, onDone, onBack }) {
  const { run } = useWallCommand();
  const items = choiceItems(e, cmd);
  const [picked, setPicked] = useState([]);
  const toggle = (id) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));
  return (
    <>
      <div className="wt-sheet-title">{cmd.label}</div>
      <div className="wt-sheet-sub">{items.length ? 'Pick one or more, then Go.' : `${e.name} hasn't listed any yet.`}</div>
      <div className="wt-choices" role="group" aria-label={cmd.label}>
        {items.map((x) => {
          const on = picked.includes(x.id);
          return (
            <button key={x.id} type="button" className={'wt-choice' + (on ? ' is-on' : '')} aria-pressed={on} onClick={() => toggle(x.id)}>
              {on ? <Icon name="check" size={18} strokeWidth={2.6} /> : null}
              {x.name}
            </button>
          );
        })}
      </div>
      <div className="wt-confirm-btns">
        <button type="button" className="wt-confirm-btn" onClick={onBack}>
          {onBack.isBack ? 'Back' : 'Cancel'}
        </button>
        <button
          type="button"
          className="wt-confirm-btn is-ok"
          disabled={!picked.length}
          onClick={() => {
            const ids = items.filter((x) => picked.includes(x.id)).map((x) => x.id);
            onDone();
            run(e, cmd, ids);
          }}
        >
          Go{picked.length ? ` · ${picked.length}` : ''}
        </button>
      </div>
    </>
  );
}

/** The sheet: every extra action, or straight to one "choices" command's chips (`start`). */
export function DeviceSheet({ e, kind, main, start, onClose, note }) {
  const { run, busy } = useWallCommand();
  const [choosing, setChoosing] = useState(start || null);
  useEffect(() => {
    const on = (ev) => ev.key === 'Escape' && onClose();
    window.addEventListener('keydown', on);
    return () => window.removeEventListener('keydown', on);
  }, [onClose]);
  const { battery, problem } = deviceVitals(e);
  const actions = [...(main ? [main] : []), ...extraActions(e, kind, main)];
  const back = start ? onClose : Object.assign(() => setChoosing(null), { isBack: true });
  const sheet = (
    <div className="wt-overlay" role="dialog" aria-modal="true" aria-label={e.name} onClick={(ev) => ev.target === ev.currentTarget && onClose()}>
      <div className="wt-confirm wt-devsheet">
        {choosing ? (
          <Chips e={e} cmd={choosing} onDone={onClose} onBack={back} />
        ) : (
          <>
            <div className="wt-sheet-title">{e.name}</div>
            <div className={'wt-sheet-sub' + (problem ? ' is-warn' : '')}>
              {entityStateText(e, kind)}
              {battery !== null ? ` · ${battery}%` : ''}
              {problem ? ` · ${problem}` : ''}
            </div>
            {note ? <div className="wt-sheet-sub is-warn">{note}</div> : null}
            <div className="wt-devsheet-btns">
              {actions.map((c) => (
                <button
                  key={c.id}
                  type="button"
                  className={'wt-confirm-btn' + (c === main ? ' is-ok' : '')}
                  disabled={e.available === false || !!busy[e.id] || (c.valueType !== 'choices' && !!c.confirmsTo && c.confirmsTo === e.state)}
                  onClick={() => {
                    if (c.valueType === 'choices') return setChoosing(c);
                    onClose();
                    return run(e, c);
                  }}
                >
                  {c.label}
                  {c.valueType === 'choices' ? '…' : ''}
                </button>
              ))}
            </div>
            <button type="button" className="wt-link-btn wt-devsheet-close" onClick={onClose}>
              Close
            </button>
          </>
        )}
      </div>
    </div>
  );
  return createPortal(sheet, document.body);
}
