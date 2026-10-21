// The "All cameras" grid: every camera (not zones) as live low-res video with an
// "N alerts today" badge; tapping one opens the full-screen live view (#/cameras/<id>).

import { useMemo } from 'react';
import { enc } from '../api.js';
import { useApi, useHub } from '../hooks.js';
import { useLiveRefresh } from '../live.js';
import { Empty } from './Controls.jsx';
import { hasStill } from './Camera.jsx';
import LiveVideo from './LiveVideo.jsx';
import { countList, isActive } from '../model.js';

// Zones and their camera are linked by data (zone.video.derivedFrom, camera.attrs.zones / zone.attrs.camera), not by kind.
export function zonesForCamera(cam, entities) {
  if (cam.attrs && cam.attrs.camera) return []; // a zone has no zones of its own
  const names = (cam.attrs && cam.attrs.zones) || [];
  return entities.filter((z) => z.id !== cam.id && ((z.video && z.video.derivedFrom === cam.id) || (z.attrs && (z.attrs.camera === cam.name || (z.attrs.camera && names.includes(z.name))))));
}

/** The camera a zone belongs to (the zone names it in attrs.camera). */
export function cameraForZone(zone, entities) {
  // The hub says which device a device's video is (video.derivedFrom); else the camera it names.
  const origin = zone.video && zone.video.derivedFrom;
  const byOrigin = origin ? entities.find((c) => c.id === origin) : null;
  if (byOrigin) return byOrigin;
  const name = zone.attrs && zone.attrs.camera;
  if (!name) return null;
  return entities.find((c) => c.id !== zone.id && c.name === name && !(c.attrs && c.attrs.camera)) || null;
}

/** Could be in All Cameras: a device with a picture (or a camera kind), except a zone shown under its camera. */
export function camerasCandidate(e, entities, kindsById) {
  return !!(e.live || hasStill(e) || (kindsById[e.kind] && kindsById[e.kind].hasCamera)) && !cameraForZone(e, entities);
}

/** Its "Show in All Cameras" setting (on unless an admin switched it off). */
export function shownInCameras(e) {
  return !(e.video && e.video.showInCameras === false);
}

/** Tiles on the Cameras page (and every All Cameras list): the candidates still shown there. */
export function cameraTiles(entities, kindsById) {
  return entities.filter((e) => camerasCandidate(e, entities, kindsById) && shownInCameras(e));
}

/** Midnight `daysBack` days ago, in unix seconds (0: today's). */
function startOfDay(daysBack = 0) {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  d.setDate(d.getDate() - daysBack);
  return d.getTime() / 1000;
}

/** Alerts from path (a device's alerts or api/alerts) since midnight `daysBack` days ago, newest first;
 *  refreshed when a live alert arrives. */
export function useRecentAlerts(path, daysBack) {
  const res = useApi(path ? path + (path.includes('?') ? '&' : '?') + 'limit=200' : null);
  useLiveRefresh((m) => m.type === 'activity' && !!m.entry && m.entry.kind === 'alert', () => path && res.reload(), 800);
  const since = startOfDay(daysBack);
  const list = useMemo(() => (Array.isArray(res.data) ? res.data.filter((a) => (a.start || 0) >= since) : null), [res.data, since]);
  return { ...res, list };
}

/** Today's alerts from path. */
export function useTodayAlerts(path) {
  return useRecentAlerts(path, 0);
}

export function ZoneLink({ zone, kind, dark }) {
  const occupied = isActive(zone, kind);
  const counts = countList(zone.attrs && zone.attrs.counts);
  return (
    <a className={'cam-zone' + (dark ? ' cam-zone--dark' : '') + (occupied ? ' is-occupied' : '')} href={'#/devices/' + enc(zone.id)}>
      <span className={'dot ' + (occupied ? 'dot--amber' : 'dot--teal')} aria-hidden="true" />
      <span className="cam-zone-name">{zone.name}</span>
      <span className="cam-zone-state">{occupied ? (counts.length ? counts.join(' · ') : zone.stateLabel || 'Occupied') : zone.stateLabel || 'Clear'}</span>
    </a>
  );
}

export default function CameraGrid({ hrefFor = (id) => '#/cameras/' + enc(id) }) {
  const { entities, kindsById } = useHub();
  const all = entities || [];
  const cams = cameraTiles(all, kindsById);
  const today = useTodayAlerts(cams.length ? 'api/alerts' : null);
  const counts = {};
  (today.list || []).forEach((a) => {
    if (a.entityId) counts[a.entityId] = (counts[a.entityId] || 0) + 1;
  });

  if (!cams.length) {
    return (
      <div className="card">
        <Empty title="No cameras yet" action={<a href="#/integrations">Add a connection</a>}>
          Connect your camera system under Connections and your cameras appear here.
        </Empty>
      </div>
    );
  }

  return (
    <div className="camgrid">
      {cams.map((cam) => {
        const n = counts[cam.id] || 0;
        return (
          <a key={cam.id} className="camgrid-tile" href={hrefFor(cam.id)} aria-label={`${cam.name}: watch full screen${n ? `, ${n} alert${n === 1 ? '' : 's'} today` : ''}`}>
            <LiveVideo entity={cam} quality="sub" stillHeight={360} showNote={false} />
            <span className="camgrid-name">
              <span className="camgrid-dot" aria-hidden="true" />
              {cam.name}
            </span>
            {n ? <span className="camgrid-badge">{n === 1 ? '1 alert today' : `${n} alerts today`}</span> : null}
          </a>
        );
      })}
    </div>
  );
}