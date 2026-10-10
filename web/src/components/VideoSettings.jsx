// A device's video settings (admins, on its page): whether it's in All Cameras, "Video from" another
// connection's live video, and which of its own streams its tiles and its full-screen view play. Each
// change is saved straight away; the hub pushes it to every open screen (players reconnect).
// "Video from" offers connections, not cameras: the hub lists each other connection with cameras
// (video.choices) and the one at this device's address when it knows it; otherwise a camera is picked
// from that connection.

import { useState } from 'react';
import { api, enc, toast } from '../api.js';
import { useHub } from '../hooks.js';
import { Switch } from './Controls.jsx';
import { camerasCandidate } from './CameraGrid.jsx';

const choicesOf = (e) => (e && e.video && e.video.choices && e.video.choices.options) || [];

/** Whether a device has any video setting to show. */
export function hasVideoSettings(e, entities, kindsById) {
  const v = e && e.video;
  if (!v) return false;
  return camerasCandidate(e, entities || [], kindsById || {}) || (v.streams || []).length > 0 || !!v.from || choicesOf(e).length > 0;
}

function StreamSelect({ label, value, streams, disabled, onChange }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      <select className="select" value={value || ''} disabled={disabled} onChange={(ev) => onChange(ev.target.value || null)}>
        <option value="">Automatic</option>
        {streams.map((s) => (
          <option key={s.id} value={s.id}>
            {s.label}
          </option>
        ))}
      </select>
    </label>
  );
}

export default function VideoSettings({ entity: e }) {
  const { entities, entitiesById, kindsById, setEntities } = useHub();
  const [busy, setBusy] = useState(false);
  const [picked, setPicked] = useState(null); // a connection chosen whose camera is still to be picked
  const v = e.video || {};
  const streams = v.streams || [];
  const options = [...choicesOf(e)];
  const ownLabel = (v.choices && v.choices.own) || 'Its own';
  const fromEnt = v.from ? entitiesById[v.from] : null;
  // The connection the saved link belongs to (shown even when it isn't offered any more).
  let current = null;
  if (v.from) {
    const iid = v.fromIntegration || (fromEnt && fromEnt.integration) || '';
    current = options.find((o) => o.integration === iid) || options.find((o) => (o.cameras || []).some((c) => c.id === v.from));
    if (!current) {
      current = { integration: iid || v.from, label: (fromEnt && fromEnt.integrationName) || iid || v.from, device: null, cameras: [] };
      options.push(current);
    }
  }
  const active = (picked && options.find((o) => o.integration === picked)) || current;
  const manual = active && (!active.device || (active === current && v.from !== active.device));
  const cams = active ? [...(active.cameras || [])] : [];
  if (manual && v.from && active === current && !cams.some((c) => c.id === v.from)) {
    cams.push({ id: v.from, name: `${fromEnt ? fromEnt.name : v.from} (no live video right now)`, gone: true });
  }

  const save = async (patch, done) => {
    setBusy(true);
    try {
      const updated = await api.put('api/entities/' + enc(e.id) + '/video', patch);
      if (updated && updated.id) setEntities((list) => (list || []).map((x) => (x.id === updated.id ? updated : x)));
      setPicked(null);
      toast(done, 'success');
    } catch {
      /* toast already shown */
    } finally {
      setBusy(false);
    }
  };
  const link = (id) => {
    const src = entitiesById[id];
    save({ videoFrom: id }, `${e.name} shows video from ${src ? src.name : id}`);
  };

  return (
    <div className="stack video-settings">
      {camerasCandidate(e, entities || [], kindsById || {}) ? (
        <Switch
          checked={v.showInCameras !== false}
          disabled={busy}
          onChange={(on) => save({ showInCameras: on }, on ? `${e.name} is shown in All Cameras` : `${e.name} is left out of All Cameras`)}
          label="Show in All Cameras"
        />
      ) : null}
      {options.length ? (
        <label className="field">
          <span className="field-label">Video from</span>
          <select
            className="select video-from"
            value={active ? active.integration : ''}
            disabled={busy}
            onChange={(ev) => {
              const o = options.find((x) => x.integration === ev.target.value);
              if (!o) {
                setPicked(null);
                if (v.from) save({ videoFrom: null }, `${e.name} shows its own video`);
              } else if (o.device) {
                if (o.device !== v.from) link(o.device);
                else setPicked(null);
              } else setPicked(o.integration);
            }}
          >
            <option value="">{ownLabel}</option>
            {options.map((o) => (
              <option key={o.integration} value={o.integration}>
                {o.label}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {manual ? (
        <label className="field">
          <span className="field-label">Camera in {active.label}</span>
          <select
            className="select video-camera"
            value={cams.some((c) => c.id === v.from) ? v.from : ''}
            disabled={busy}
            onChange={(ev) => ev.target.value && link(ev.target.value)}
          >
            {cams.some((c) => c.id === v.from) ? null : <option value="">Choose a camera</option>}
            {cams.map((c) => (
              <option key={c.id} value={c.id} disabled={!!c.gone}>
                {c.name}
              </option>
            ))}
          </select>
        </label>
      ) : null}
      {streams.length ? (
        <>
          <StreamSelect label="Tile stream" value={v.tileStream} streams={streams} disabled={busy} onChange={(id) => save({ tileStream: id }, `Tile stream: ${id || 'Automatic'}`)} />
          <StreamSelect label="Full-screen stream" value={v.fullStream} streams={streams} disabled={busy} onChange={(id) => save({ fullStream: id }, `Full-screen stream: ${id || 'Automatic'}`)} />
        </>
      ) : null}
    </div>
  );
}