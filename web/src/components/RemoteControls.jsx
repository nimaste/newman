// A remote's Controls (its page, left pane): which lights it controls and what each press does - a
// setting of the remote, not automations. The hub works out the standard thing from each button's role
// (attrs.buttons: on / off / raise / lower); any press can instead control another device, run an
// automation or do nothing. Admins edit here; the phone apps only show it.

import { useMemo, useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { Check, Switch } from './Controls.jsx';

/** Remotes whose connection says what their buttons are (or that already have Controls). */
export function hasRemoteControls(e) {
  return !!(e && (e.controls || (e.attrs && Array.isArray(e.attrs.buttons) && e.attrs.buttons.length)));
}

/** A press's standard job in words - the same words the hub uses (controls.py, standard). */
function standardText(button, hold, c) {
  const role = button && button.role;
  if (!c.lights.length || !['on', 'off', 'raise', 'lower'].includes(role)) return 'Nothing';
  if (hold) {
    if ((role === 'on' || role === 'off') && !c.hold) return 'Nothing';
    return role === 'on' || role === 'raise' ? 'Brighten until you let go' : 'Dim until you let go';
  }
  if (role === 'on') return c.onLevel ? `On at ${c.onLevel}%` : 'On';
  if (role === 'off') return 'Off';
  return role === 'raise' ? 'A bit brighter' : 'A bit dimmer';
}

function LightChips({ ids }) {
  const { entitiesById } = useHub();
  return (
    <div className="rc-chips">
      {ids.map((id) => (
        <a key={id} className="rc-chip" href={'#/devices/' + enc(id)}>
          {(entitiesById[id] && entitiesById[id].name) || id}
        </a>
      ))}
    </div>
  );
}

export default function RemoteControls({ entity: e, isAdmin }) {
  const [editing, setEditing] = useState(false);
  const c = e.controls;
  const n = c ? c.lights.length : 0;
  return (
    <div className="rc-box" aria-label="Controls">
      <div className="rc-head">
        <div>
          <div className="eyebrow eyebrow--sm">Controls</div>
          {!editing ? <div className="rc-title">{c ? (n ? `${n} light${n === 1 ? '' : 's'}` : 'Buttons only') : 'Not controlling anything yet'}</div> : null}
        </div>
        {isAdmin && !editing ? (
          <button type="button" className="btn btn-sm2" onClick={() => setEditing(true)}>
            {c ? 'Edit' : 'Set up'}
          </button>
        ) : null}
      </div>
      {editing ? (
        <ControlsEditor entity={e} onDone={() => setEditing(false)} />
      ) : c ? (
        <>
          {n ? <LightChips ids={c.lights} /> : null}
          <div className="rc-rows">
            {c.rows.map((r) => (
              <div key={r.key} className="rc-row">
                <span className="rc-press">{r.label}</span>
                <span className={'rc-does' + (r.custom ? ' is-custom' : '')}>{r.does}</span>
              </div>
            ))}
          </div>
        </>
      ) : (
        <div className="help">Pick the lights its buttons switch and dim, or give each button its own job.</div>
      )}
    </div>
  );
}

function ControlsEditor({ entity: e, onDone }) {
  const { kindsById, reloadEntities } = useHub();
  const res = useApi('api/entities/' + enc(e.id) + '/controls');
  if (!res.data) return res.error ? <div className="warn">{res.error.message}</div> : <div className="muted">Loading…</div>;
  return <EditorForm entity={e} data={res.data} kindsById={kindsById} onDone={onDone} reload={reloadEntities} />;
}

function EditorForm({ entity: e, data, kindsById, onDone, reload }) {
  const saved = data.controls;
  const [lights, setLights] = useState(saved ? saved.lights : []);
  const [lastLevel, setLastLevel] = useState(saved ? saved.onLevel === null || saved.onLevel === undefined : false);
  const [level, setLevel] = useState(saved && saved.onLevel ? saved.onLevel : 60);
  const [hold, setHold] = useState(saved ? saved.hold !== false : true);
  const [buttons, setButtons] = useState(saved ? saved.buttons || {} : {});
  const [find, setFind] = useState('');
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  const devices = data.choices.devices;
  const byId = useMemo(() => Object.fromEntries(devices.map((d) => [d.id, d])), [devices]);
  const area = (e.attrs && e.attrs.area) || '';
  const switchable = devices.filter((d) => d.switchable && (!find || d.name.toLowerCase().includes(find.toLowerCase())));
  const here = area ? switchable.filter((d) => d.area === area) : [];
  const elsewhere = switchable.filter((d) => !here.includes(d));
  const roles = Object.fromEntries((data.buttons || []).map((b) => [b.id, b]));
  const current = { lights, onLevel: lastLevel ? null : level, hold };

  const toggle = (id) => setLights((l) => (l.includes(id) ? l.filter((x) => x !== id) : [...l, id]));
  const setPress = (key, o) =>
    setButtons((b) => {
      const next = { ...b };
      if (o) next[key] = o;
      else delete next[key];
      return next;
    });

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.put('api/entities/' + enc(e.id) + '/controls', { ...current, buttons }, { quiet: true });
      toast(`Saved ${e.name}'s controls`, 'success');
      reload && reload();
      onDone();
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  };
  const remove = async () => {
    setBusy(true);
    try {
      await api.del('api/entities/' + enc(e.id) + '/controls');
      toast(`${e.name} doesn't control anything now`, 'success');
      reload && reload();
      onDone();
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false);
    }
  };

  const group = (title, list) =>
    list.length ? (
      <>
        <div className="rc-group">{title}</div>
        {list.map((d) => (
          <div key={d.id} className="rc-pick">
            <Check checked={lights.includes(d.id)} onChange={() => toggle(d.id)} strong={lights.includes(d.id)}>
              {d.name}
            </Check>
          </div>
        ))}
      </>
    ) : null;

  return (
    <div className="stack rc-edit">
      <div className="rc-title">What this remote controls</div>
      <div className="field">
        <span className="field-label">Lights</span>
        <input className="input" type="search" placeholder="Find a light" value={find} onChange={(ev) => setFind(ev.target.value)} aria-label="Find a light" />
        <div className="rc-list" role="group" aria-label="Lights">
          {group(area ? area.toUpperCase() : 'LIGHTS', here.length ? here : elsewhere)}
          {here.length ? group('OTHER ROOMS', elsewhere) : null}
          {!switchable.length ? <div className="muted rc-pick">No lights match.</div> : null}
        </div>
      </div>
      <fieldset className="field rc-fieldset">
        <legend className="field-label">When it turns them on</legend>
        <label className="rc-radio">
          <input type="radio" name="rc-level" checked={!lastLevel} onChange={() => setLastLevel(false)} />
          At a set brightness
        </label>
        {!lastLevel ? (
          <div className="rc-level">
            <input type="range" min="1" max="100" value={level} onChange={(ev) => setLevel(Number(ev.target.value))} aria-label="Brightness" />
            <span className="rc-level-value">{level}%</span>
          </div>
        ) : null}
        <label className="rc-radio">
          <input type="radio" name="rc-level" checked={lastLevel} onChange={() => setLastLevel(true)} />
          At the brightness they had last
        </label>
      </fieldset>
      <div className="rc-hold">
        <Switch checked={hold} onChange={setHold} label="Hold to brighten and dim" />
      </div>
      <div className="field">
        <span className="field-label">Buttons</span>
        <div className="help">Each press does the standard thing for the lights above, unless you pick something else.</div>
        <div className="rc-presses">
          {data.rows.map((r) => {
            const isHold = r.key.endsWith('_long');
            const b = roles[isHold ? r.key.slice(0, -5) : r.key];
            return (
              <PressRow
                key={r.key}
                press={r}
                isHold={isHold}
                standard={standardText(b, isHold, current)}
                value={buttons[r.key]}
                onChange={(o) => setPress(r.key, o)}
                devices={devices}
                byId={byId}
                automations={data.choices.automations}
                kindsById={kindsById}
              />
            );
          })}
        </div>
      </div>
      {error ? (
        <div className="warn" role="alert">
          {error}
        </div>
      ) : null}
      <div className="rc-foot">
        {saved ? (
          <button type="button" className="btn btn-sm2 btn-danger" onClick={remove} disabled={busy}>
            Remove controls
          </button>
        ) : (
          <span />
        )}
        <div className="rc-foot-right">
          <button type="button" className="btn btn-sm2" onClick={onDone} disabled={busy}>
            Cancel
          </button>
          <button type="button" className="btn btn-sm2 btn-primary" onClick={save} disabled={busy}>
            Save
          </button>
        </div>
      </div>
    </div>
  );
}

function PressRow({ press, isHold, standard, value, onChange, devices, byId, automations, kindsById }) {
  const kind = value ? value.do : 'standard';
  const dev = value && value.entity ? byId[value.entity] : null;
  const cmds = dev ? dev.commands.filter((c) => isHold || !c.holdOnly) : [];
  const cmd = dev ? cmds.find((c) => c.id === value.command) : null;
  const id = 'rc-' + press.key;
  const pick = (what) => {
    if (what === 'standard') onChange(null);
    else if (what === 'nothing') onChange({ do: 'nothing' });
    else if (what === 'automation') onChange({ do: 'automation', automation: (automations[0] || {}).id || '' });
    else {
      const d = devices.find((x) => x.commands.length) || null;
      onChange({ do: 'device', entity: d ? d.id : '', command: d ? d.commands[0].id : '' });
    }
  };
  const valueInput = () => {
    if (!cmd || !cmd.takesValue) return null;
    const label = cmd.label;
    if (cmd.valueType === 'state') {
      const states = ((kindsById[dev.kind] || {}).states || []);
      return (
        <select className="select" aria-label={label} value={value.value ?? ''} onChange={(ev) => onChange({ ...value, value: ev.target.value })}>
          <option value="">Choose…</option>
          {states.map((s) => (
            <option key={s.id} value={s.id}>
              {s.label}
            </option>
          ))}
        </select>
      );
    }
    const numeric = cmd.valueType === 'percent' || cmd.valueType === 'number';
    return (
      <span className="rc-value">
        <input
          className="input"
          aria-label={label}
          type={numeric ? 'number' : 'text'}
          min={cmd.min ?? undefined}
          max={cmd.max ?? undefined}
          step={cmd.step ?? undefined}
          value={value.value ?? ''}
          onChange={(ev) => onChange({ ...value, value: numeric ? (ev.target.value === '' ? '' : Number(ev.target.value)) : ev.target.value })}
        />
        {cmd.unit ? <span className="muted">{cmd.unit}</span> : null}
      </span>
    );
  };
  return (
    <div className={'rc-press-row' + (value ? ' is-custom' : '')}>
      <div className="rc-press-line">
        <label htmlFor={id} className="rc-press">
          {press.label}
        </label>
        <select id={id} className="select" value={kind} onChange={(ev) => pick(ev.target.value)}>
          <option value="standard">Standard: {standard}</option>
          <option value="device">Control a device</option>
          <option value="automation">Run an automation</option>
          <option value="nothing">Nothing</option>
        </select>
      </div>
      {kind === 'device' ? (
        <div className="rc-press-more">
          <select
            className="select"
            aria-label={`${press.label}: device`}
            value={value.entity}
            onChange={(ev) => {
              const d = byId[ev.target.value];
              const first = d ? d.commands.filter((c) => isHold || !c.holdOnly)[0] : null;
              onChange({ do: 'device', entity: ev.target.value, command: first ? first.id : '' });
            }}
          >
            {devices.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name}
              </option>
            ))}
          </select>
          <select
            className="select"
            aria-label={`${press.label}: what to do`}
            value={value.command}
            onChange={(ev) => onChange({ do: 'device', entity: value.entity, command: ev.target.value })}
          >
            {cmds.map((c) => (
              <option key={c.id} value={c.id}>
                {c.label}
              </option>
            ))}
          </select>
          {valueInput()}
        </div>
      ) : null}
      {kind === 'automation' ? (
        <div className="rc-press-more">
          <select className="select" aria-label={`${press.label}: automation`} value={value.automation} onChange={(ev) => onChange({ do: 'automation', automation: ev.target.value })}>
            {!automations.length ? <option value="">No automations yet</option> : null}
            {automations.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </select>
        </div>
      ) : null}
    </div>
  );
}
