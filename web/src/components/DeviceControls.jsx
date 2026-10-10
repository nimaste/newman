// Device controls shared by the Devices table, integration pages and the device page.
// Everything is derived from the kind's commands in /api/meta: valueType picks the control
// (percent slider, number stepper, state picker); an on/off pair becomes a switch.

import { useCallback, useEffect, useRef, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useHub } from '../hooks.js';
import { useConfirm } from './Feedback.jsx';
import { Switch } from './Controls.jsx';
import { Icon } from '../icons.jsx';
import { choiceCommands, commandCurrent, plainCommands, stateChoices, toggleDuo, valueText } from '../model.js';
import { ChoicesButton } from './ChoiceChips.jsx';

/** run(entity, command, value?) with confirm for sensitive commands, toasts, busy state and a refresh. */
export function useDeviceActions() {
  const { reloadEntities, setEntities, kindsById } = useHub();
  const confirm = useConfirm();
  const [busy, setBusy] = useState({});

  const run = useCallback(
    async (e, cmd, value) => {
      if (cmd.sensitive) {
        const ok = await confirm({
          title: `${cmd.label} ${e.name}?`,
          body: `Newman will send “${cmd.label}” to ${e.name} right away.`,
          confirmLabel: cmd.label,
          danger: true,
          sensitive: true,
        });
        if (!ok) return;
      }
      setBusy((b) => ({ ...b, [e.id]: cmd.id }));
      try {
        await api.post('api/entities/' + enc(e.id) + '/command', value === undefined ? { command: cmd.id } : { command: cmd.id, value });
        const shown = valueText(cmd, kindsById[e.kind], value, e);
        toast(value === undefined ? `${cmd.label} sent to ${e.name}` : `${e.name}: ${cmd.label.toLowerCase()} ${shown}`, 'success');
      } catch {
        /* toast already shown (409 carries the hub's reason) */
      } finally {
        setBusy((b) => ({ ...b, [e.id]: null }));
        reloadEntities();
      }
    },
    [confirm, reloadEntities, kindsById],
  );

  const rename = useCallback(
    async (e, name) => {
      try {
        await api.patch('api/entities/' + enc(e.id), { name });
        setEntities((list) => (list || []).map((x) => (x.id === e.id ? { ...x, name } : x)));
        toast(`Renamed to ${name}`, 'success');
        reloadEntities();
      } catch {
        /* toast already shown */
      }
    },
    [reloadEntities, setEntities],
  );

  return { run, rename, busy };
}

/** Device name, linking to its page, with an inline rename button. */
export function EntityName({ entity, onRename, link = true, className = 'cell-name' }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(entity.name);

  const commit = async () => {
    const v = value.trim();
    setEditing(false);
    if (v && v !== entity.name) await onRename(v);
  };

  if (editing) {
    return (
      <div className={className}>
        <input
          className="input input--sm"
          aria-label={`New name for ${entity.name}`}
          value={value}
          autoFocus
          onChange={(e) => setValue(e.target.value)}
          onBlur={commit}
          onKeyDown={(e) => {
            if (e.key === 'Enter') e.currentTarget.blur();
            if (e.key === 'Escape') {
              setValue(entity.name);
              setEditing(false);
            }
          }}
        />
      </div>
    );
  }
  return (
    <div className={className}>
      {link ? (
        <a className="device-link" href={'#/devices/' + enc(entity.id)} title={`Open ${entity.name}`}>
          {entity.name}
        </a>
      ) : (
        <span title={entity.name}>{entity.name}</span>
      )}
      {onRename ? (
        <button
          type="button"
          className="icon-btn"
          aria-label={`Rename ${entity.name}`}
          title="Rename"
          onClick={() => {
            setValue(entity.name);
            setEditing(true);
          }}
        >
          <Icon name="pencil" size={15} />
        </button>
      ) : null}
    </div>
  );
}

/**
 * Percent slider (valueType "percent") that only sends a command when released
 * (pointer, touch or key). Range, step and unit come from the command.
 */
function ValueSlider({ entity, command, onCommit }) {
  const min = command.min ?? 0;
  const max = command.max ?? 100;
  const cur = commandCurrent(entity, command);
  const current = typeof cur === 'number' ? Math.round(cur) : min;
  const [value, setValue] = useState(current);
  const dragging = useRef(false);
  const lastSent = useRef(current);
  // Latest value in a ref: key/pointer release can fire before React re-renders.
  const latest = useRef(current);

  // Follow the hub's value only when it changes and the user isn't mid-drag,
  // so the slider doesn't jump back while the command is in flight.
  useEffect(() => {
    if (!dragging.current) {
      setValue(current);
      latest.current = current;
      lastSent.current = current;
    }
  }, [current]);

  const commit = () => {
    dragging.current = false;
    const v = latest.current;
    if (v !== lastSent.current) {
      lastSent.current = v;
      onCommit(v);
    }
  };
  const shown = valueText(command, null, value);

  return (
    <label className="slider" title={`${command.label}: ${shown}`}>
      <span className="sr-only">
        {command.label} for {entity.name}
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={command.step || 1}
        value={value}
        disabled={entity.available === false}
        onChange={(ev) => {
          dragging.current = true;
          latest.current = Number(ev.target.value);
          setValue(latest.current);
        }}
        onPointerUp={commit}
        onTouchEnd={commit}
        onKeyUp={commit}
        onBlur={() => dragging.current && commit()}
        aria-valuetext={shown}
      />
      <span className="slider-value">{shown}</span>
    </label>
  );
}

/** − / + stepper (valueType "number") using the command's min/max/step/unit; sends once the clicking stops (~800 ms). */
function ValueStepper({ entity, command, onCommit }) {
  const cur = commandCurrent(entity, command);
  const current = typeof cur === 'number' ? cur : null;
  const [value, setValue] = useState(current);
  const timer = useRef(null);
  const pending = useRef(false);

  useEffect(() => {
    if (!pending.current) setValue(current);
  }, [current]);
  useEffect(() => () => clearTimeout(timer.current), []);

  if (current === null && value === null) return null;
  const stepBy = command.step || 1;
  const decimals = String(stepBy).includes('.') ? String(stepBy).split('.')[1].length : 0;
  const step = (d) => {
    let next = Math.round(((value ?? current ?? command.default ?? 0) + d) / stepBy) * stepBy;
    if (typeof command.min === 'number') next = Math.max(command.min, next);
    if (typeof command.max === 'number') next = Math.min(command.max, next);
    next = Number(next.toFixed(decimals));
    setValue(next);
    pending.current = true;
    clearTimeout(timer.current);
    timer.current = setTimeout(() => {
      pending.current = false;
      if (next !== current) onCommit(next);
    }, 800);
  };
  const off = entity.available === false;
  return (
    <span className="stepper" role="group" aria-label={`${command.label} for ${entity.name}`}>
      <button type="button" className="stepper-btn" onClick={() => step(-stepBy)} disabled={off} aria-label={`Lower: ${command.label}`}>
        −
      </button>
      <span className="stepper-value" aria-live="polite">
        {value !== null ? value.toFixed(decimals) : '—'}
        <small>{command.unit || ''}</small>
      </span>
      <button type="button" className="stepper-btn" onClick={() => step(stepBy)} disabled={off} aria-label={`Raise: ${command.label}`}>
        +
      </button>
    </span>
  );
}

/**
 * The on/off pair shown as a switch: two plain, non-sensitive commands, one confirming
 * to the kind's active state and the other to a different state. Found from meta, not by id.
 */
function switchPair(kind, plain) {
  if (!kind || !kind.activeState || plain.length !== 2 || plain.some((c) => c.sensitive)) return null;
  const on = plain.find((c) => c.confirmsTo === kind.activeState);
  const off = plain.find((c) => c !== on && c.confirmsTo && c.confirmsTo !== kind.activeState);
  return on && off ? { on, off } : null;
}

/** A command that takes free text (a webhook's "Send"): a small input and the button. */
function TextCommand({ entity, command, onSend, disabled }) {
  const [text, setText] = useState('');
  return (
    <form
      className="text-cmd"
      onSubmit={(ev) => {
        ev.preventDefault();
        onSend(text);
        setText('');
      }}
    >
      <input className="input input--sm" value={text} onChange={(ev) => setText(ev.target.value)} placeholder="Message (optional)" aria-label={`${command.label}: message for ${entity.name}`} disabled={disabled} />
      <button type="submit" className="btn btn-sm" disabled={disabled}>
        <Icon name={command.icon || 'send'} size={14} /> {command.label}
      </button>
    </form>
  );
}

/** Row controls built from the kind's commands and their valueType. */
export function DeviceControls({ entity: e, kind, busy, run, size = 'sm', compact = false }) {
  const cmds = (kind && kind.commands) || [];
  const plain = plainCommands(kind);
  const pair = switchPair(kind, plain);
  const isOn = !!kind && e.state === kind.activeState;
  // A device can say it lacks a value control (a light that isn't dimmable).
  const dimmable = !(e.attrs && e.attrs.dimmable === false);
  const sliders = cmds.filter((c) => c.valueType === 'percent' && dimmable);
  const steppers = compact ? [] : cmds.filter((c) => c.valueType === 'number');
  const pickers = compact ? [] : cmds.filter((c) => c.valueType === 'state');
  const duo = pair ? null : toggleDuo(kind, plain);
  const buttons = plain.filter((c) => (!pair || (c.id !== pair.on.id && c.id !== pair.off.id)) && (!duo || c.id !== (isOn ? duo.on.id : duo.off.id)));
  const choices = compact ? [] : choiceCommands(kind);
  const texts = compact ? [] : cmds.filter((c) => c.valueType === 'text');
  // A play / pause pair is one button that says what it will do, not an on/off switch.
  const media = pair && /play/.test(String(pair.on.icon || '')) && /pause/.test(String(pair.off.icon || ''));
  const offline = e.available === false;
  const modes = stateChoices(kind);

  return (
    <>
      {modes.length
        ? pickers.map((pc) => (
            <div key={pc.id} className={'seg seg--mini' + (size === 'lg' ? ' seg--lg' : '')} role="radiogroup" aria-label={`${pc.label} for ${e.name}`} style={{ gridTemplateColumns: `repeat(${modes.length}, auto)` }}>
              {modes.map((m) => (
                <button key={m.id} type="button" role="radio" aria-checked={e.state === m.id} disabled={offline || !!busy} onClick={() => e.state !== m.id && run(e, pc, m.id)}>
                  {m.label}
                </button>
              ))}
            </div>
          ))
        : null}
      {steppers.map((c) => (
        <ValueStepper key={c.id} entity={e} command={c} onCommit={(v) => run(e, c, v)} />
      ))}
      {sliders.map((c) => (
        <ValueSlider key={c.id} entity={e} command={c} onCommit={(v) => run(e, c, v)} />
      ))}
      {pair && media ? (
        <button type="button" className={'btn ' + (size === 'lg' ? '' : 'btn-sm')} disabled={offline || !!busy} onClick={() => run(e, isOn ? pair.off : pair.on)} aria-label={`${isOn ? pair.off.label : pair.on.label} ${e.name}`}>
          <Icon name={isOn ? pair.off.icon : pair.on.icon} size={14} />
          {isOn ? pair.off.label : pair.on.label}
        </button>
      ) : pair ? (
        <Switch checked={isOn} disabled={offline || !!busy} onChange={(v) => run(e, v ? pair.on : pair.off)} ariaLabel={`${e.name}: ${isOn ? pair.off.label : pair.on.label}`} />
      ) : null}
      {buttons.map((c) => (
        <button
          key={c.id}
          type="button"
          className={'btn ' + (size === 'lg' ? '' : 'btn-sm') + (c.sensitive ? ' btn-danger' : '')}
          disabled={offline || !!busy || (c.confirmsTo && e.state === c.confirmsTo)}
          onClick={() => run(e, c)}
          aria-label={`${c.label} ${e.name}`}
        >
          {busy === c.id ? <span className="spinner" style={{ width: 14, height: 14 }} /> : <Icon name={c.icon || c.id} size={14} />}
          {c.label}
        </button>
      ))}
      {texts.map((c) => (
        <TextCommand key={c.id} entity={e} command={c} disabled={offline || !!busy} onSend={(v) => run(e, c, v)} />
      ))}
      {choices.map((c) => (
        <ChoicesButton key={c.id} entity={e} cmd={c} size={size} disabled={offline || !!busy} busy={busy === c.id} onGo={(ids) => run(e, c, ids)} />
      ))}
    </>
  );
}
