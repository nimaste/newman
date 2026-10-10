import { useState } from 'react';
import { enc } from '../api.js';
import { devicesListHref, navigate, useActivityFeed, useApi, useHub } from '../hooks.js';
import { useAuth } from '../auth.jsx';
import { Empty, ErrorBox, Loading, Tabs } from '../components/Controls.jsx';
import { DeviceControls, EntityName, useDeviceActions } from '../components/DeviceControls.jsx';
import { StateText } from '../components/DeviceList.jsx';
import { ActivityItem, CountChips, hasStill } from '../components/Camera.jsx';
import LiveVideo from '../components/LiveVideo.jsx';
import { AlertList } from '../components/AlertCard.jsx';
import { cameraForZone, ZoneLink, zonesForCamera } from '../components/CameraGrid.jsx';
import VideoSettings, { hasVideoSettings } from '../components/VideoSettings.jsx';
import LiveStatusSettings, { hasLiveStatus } from '../components/LiveStatusSettings.jsx';
import RemoveDevicesDialog, { isOffline } from '../components/RemoveDevices.jsx';
import { Icon } from '../icons.jsx';
import { fmtWhen } from '../format.js';
import { eventsFor, issuesOf } from '../model.js';
import { formatPropValue, triggerItems } from '../automation.js';

// Attributes worth showing, in this order; anything else scalar is shown after them.
const ATTR_LABELS = {
  currentTemp: 'Temperature',
  targetTemp: 'Target',
  heatTo: 'Heat to',
  coolTo: 'Cool to',
  humidity: 'Humidity',
  action: 'Right now',
  brightness: 'Brightness',
  batteryLevel: 'Battery',
  batteryStatus: 'Battery status',
  batteryLow: 'Battery low',
  batteryType: 'Battery type',
  acStatus: 'Mains power',
  tamperStatus: 'Tamper',
  commStatus: 'Connection',
  linkQuality: 'Signal',
  model: 'Model',
  firmware: 'Firmware',
};
const HIDDEN = new Set(['events', 'area', 'dimmable', 'unit', 'counts', 'zones', 'streams', 'camera']);
const PERCENT = new Set(['batteryLevel', 'brightness', 'humidity']);
const TEMPS = new Set(['currentTemp', 'targetTemp', 'heatTo', 'coolTo', 'temperature']);

/** The device's alerts list (the hub gives a URL when a connection keeps alerts for it). */
function alertsPathFor(e) {
  return (e && e.alerts) || null;
}

function humanize(key) {
  const s = key.replace(/_/g, ' ').replace(/([a-z])([A-Z])/g, '$1 $2').toLowerCase();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

function attrRows(entity, kind) {
  const a = entity.attrs || {};
  const unit = a.unit || (kind && kind.unit) || '°C';
  const keys = [...Object.keys(ATTR_LABELS).filter((k) => k in a), ...Object.keys(a).filter((k) => !(k in ATTR_LABELS) && !HIDDEN.has(k))];
  return keys
    .map((k) => {
      const v = a[k];
      if (v === null || v === undefined || v === '' || typeof v === 'object') return null;
      if (k === 'brightness' && a.dimmable === false) return null;
      let text;
      if (typeof v === 'boolean') text = v ? 'Yes' : 'No';
      else if (PERCENT.has(k) && typeof v === 'number') text = `${Math.round(v)}%`;
      else if (TEMPS.has(k) && typeof v === 'number') text = `${v.toFixed(1)} ${unit}`;
      else if (typeof v === 'string') text = v === 'ok' ? 'OK' : v.charAt(0).toUpperCase() + v.slice(1);
      else text = String(v);
      return { key: k, label: ATTR_LABELS[k] || humanize(k), text };
    })
    .filter(Boolean);
}

export default function DeviceDetail({ id }) {
  const { entities, entitiesById, kindsById, reloadEntities, entitiesError } = useHub();
  const { isAdmin } = useAuth();
  const autos = useApi('api/automations');
  // Devices whose camera system keeps alerts show those; other devices their recent activity.
  const isCam = !!(entitiesById[id] && alertsPathFor(entitiesById[id]));
  const events = useActivityFeed(200, { enabled: !isCam });
  const { run, rename, busy } = useDeviceActions();
  const [tab, setTab] = useState(null);
  const [removing, setRemoving] = useState(false);

  const crumb = (
    <a className="crumb" href={devicesListHref()}>
      Devices /
    </a>
  );

  if (!entities) {
    return (
      <div className="app-page">
        {crumb}
        {entitiesError ? <ErrorBox error={entitiesError} onRetry={reloadEntities} /> : <Loading>Loading device…</Loading>}
      </div>
    );
  }
  const e = entitiesById[id];
  if (!e) {
    return (
      <div className="app-page">
        {crumb}
        <div className="card">
          <Empty title="Device not found" action={<a href="#/devices">All devices</a>}>
            It may have been removed from its connection.
          </Empty>
        </div>
      </div>
    );
  }

  const kind = kindsById[e.kind];
  const issues = issuesOf(e);
  const area = e.attrs && e.attrs.area;
  const list = autos.data || [];
  const byId = Object.fromEntries(list.map((a) => [a.id, a]));
  const used = (e.usedBy || []).map((aid) => byId[aid] || { id: aid, name: aid });
  const evs = eventsFor(e, kind);
  const hasControls = ((kind && kind.commands) || []).length > 0;
  const canWatch = kind && (kind.states || []).length > 1 && !evs.length;
  // Values it reports (entity.properties from the hub), each usable in an automation; the rest of
  // its attributes (model, firmware…) are shown as plain details.
  const props = Array.isArray(e.properties) ? e.properties : [];
  const propAttrs = new Set(props.map((p) => String(p.attr || '').split('.')[0]));
  const attrs = attrRows(e, kind).filter((r) => !propAttrs.has(r.key));
  // Up front: battery, a reported problem, and every other "%" level (filter left, humidity…) as a
  // small bar — except values the controls already set (brightness).
  const setAttrs = new Set(((kind && kind.commands) || []).map((c) => c.attr).filter(Boolean));
  const battery = props.find((p) => p.id === 'battery' && typeof p.value === 'number');
  const problem = props.find((p) => p.id === 'problem' && p.value);
  const levels = props.filter((p) => p.unit === '%' && typeof p.value === 'number' && p !== battery && !setAttrs.has(p.attr));
  const mine = (events.data || [])
    .filter((x) => x.entityId === id)
    .slice(0, 20);
  const own = hasStill(e) || !!e.live;
  // A zone names its camera (attrs.camera): link to it, and borrow its picture if the zone has none.
  const parentCam = cameraForZone(e, entities);
  const camForZone = !own ? parentCam : null;
  const alertsPath = alertsPathFor(e);
  const zones = own ? zonesForCamera(e, entities) : [];

  const eventAutos = (evId) =>
    list.filter((a) => triggerItems(a.trigger).some((i) => i.type === 'event' && i.entity === id && (i.event === evId || (!i.event && evs.length === 1))));

  const tabs = [
    ...(alertsPath ? [{ id: 'alerts', label: 'Recent alerts' }] : []),
    ...(evs.length ? [{ id: 'report', label: 'Events' }] : []),
    { id: 'automations', label: `Automations (${used.length})` },
    ...(!isCam ? [{ id: 'activity', label: 'Activity' }] : []),
    ...(props.length || attrs.length ? [{ id: 'details', label: props.length ? 'Values & details' : 'Details' }] : []),
    ...(isAdmin && hasVideoSettings(e, entities, kindsById) ? [{ id: 'video', label: 'Video' }] : []),
    ...(isAdmin && hasLiveStatus(e) ? [{ id: 'live', label: 'Live status' }] : []),
  ];
  const current = tabs.some((t) => t.id === tab) ? tab : tabs[0].id;

  return (
    <div className="app-page">
      <div className="app-head">
        {crumb}
        <EntityName entity={e} onRename={isAdmin ? (n) => rename(e, n) : null} link={false} className="device-title" />
        <span className="device-meta2">
          {kind ? kind.name : e.kind}
          {area ? ' · ' + area : ''} ·{' '}
          <a href={'#/integrations/' + enc(e.integration)}>{e.integrationName || e.integration}</a>
        </span>
      </div>

      <div className={'dev-grid' + (own ? ' dev-grid--cam' : '')}>
        <section className="pane" aria-label="Right now">
          <div className="pane-body stack">
            {own ? (
              <div className="cam-hero">
                <a className="cam-hero-link" href={'#/cameras/' + enc(e.id)} aria-label={`Watch ${e.name} full screen`}>
                  <LiveVideo entity={e} quality="sub" stillHeight={720} className="cam-still--hero" />
                </a>
                <div className="cam-hero-bar">
                  <span className="cam-counts">
                    {e.attrs && e.attrs.counts ? <CountChips counts={e.attrs.counts} empty="Nothing detected right now" /> : null}
                  </span>
                  <span className="cam-hero-zones">
                    {parentCam ? (
                      <a className="cam-zone cam-zone--dark" href={'#/devices/' + enc(parentCam.id)}>
                        <span className="cam-zone-name">Camera: {parentCam.name}</span>
                      </a>
                    ) : null}
                    {zones.map((z) => (
                      <ZoneLink key={z.id} zone={z} kind={kindsById[z.kind]} dark />
                    ))}
                  </span>
                </div>
              </div>
            ) : null}
            {camForZone ? (
              <a href={'#/devices/' + enc(camForZone.id)} className="cam-tile-link cam-hero">
                <LiveVideo entity={camForZone} quality="sub" stillHeight={480} />
                <div className="cam-overlay">
                  <span className="cam-name">{camForZone.name}</span>
                </div>
              </a>
            ) : null}
            <div>
              <div className="eyebrow eyebrow--sm">Right now</div>
              <div className="device-state-value">
                <StateText entity={e} kind={kind} long />
                {battery ? (
                  <span className={'dev-battery' + (battery.value <= 20 ? ' is-low' : '')} title="Battery">
                    <span className="dev-battery-cell" aria-hidden="true">
                      <span style={{ width: Math.max(0, Math.min(100, battery.value)) + '%' }} />
                    </span>
                    {Math.round(battery.value)}%
                  </span>
                ) : null}
              </div>
              {e.updatedAt ? <div className="help">Updated {fmtWhen(e.updatedAt)}</div> : null}
            </div>
            {problem ? (
              <div className="warn" role="note">
                <Icon name="alert" size={16} />
                <span>{String(problem.value)}</span>
              </div>
            ) : null}
            {issues.length ? (
              <div className="warn" role="note">
                <Icon name="alert" size={16} />
                <span>{issues.join(' · ')}</span>
              </div>
            ) : null}
            {isAdmin && isOffline(e) ? (
              <button type="button" className="btn btn-sm2 btn-danger" style={{ alignSelf: 'flex-start' }} onClick={() => setRemoving(true)}>
                Remove device…
              </button>
            ) : null}
            {removing ? (
              <RemoveDevicesDialog
                devices={[e]}
                onClose={() => setRemoving(false)}
                onDone={() => {
                  setRemoving(false);
                  navigate(devicesListHref().replace(/^#\/?/, ''));
                }}
              />
            ) : null}
            {hasControls ? (
              <div className="device-controls">
                <DeviceControls entity={e} kind={kind} busy={busy[e.id]} run={run} size="lg" />
              </div>
            ) : null}
            {levels.length ? (
              <div className="dev-levels" aria-label="Levels">
                {levels.map((p) => (
                  <div key={p.id} className={'dev-level' + (p.value <= 10 ? ' is-low' : '')}>
                    <span className="dev-level-label">{p.label}</span>
                    <span className="dev-level-bar" aria-hidden="true">
                      <span style={{ width: Math.max(0, Math.min(100, p.value)) + '%' }} />
                    </span>
                    <span className="dev-level-value">{Math.round(p.value)}%</span>
                  </div>
                ))}
              </div>
            ) : null}
          </div>
        </section>

        <section className="pane" aria-label="More about this device">
          <div className="conn-tabs">
            <Tabs label="Device sections" value={current} onChange={setTab} tabs={tabs} />
          </div>
          <div className="pane-body">
            {current === 'video' ? <VideoSettings entity={e} /> : null}
            {current === 'live' ? <LiveStatusSettings entity={e} /> : null}
            {current === 'alerts' ? (
              <AlertList path={alertsPath} emptyText={`No alerts for ${e.name} yet.`} />
            ) : null}
            {current === 'report' ? (
              <div className="rows">
                <div className="help" style={{ paddingBottom: 6 }}>
                  Make something happen when one of these happens.
                </div>
                {evs.map((ev) => {
                  const acts = eventAutos(ev.id);
                  return (
                    <div key={ev.id} className="row event-row">
                      <div style={{ minWidth: 0 }}>
                        <div className="strong">{ev.label}</div>
                        <div className="muted" style={{ fontSize: 13 }}>
                          {acts.length
                            ? acts.map((a, i) => (
                                <span key={a.id}>
                                  {i ? ', ' : ''}
                                  <a href={'#/automations/' + enc(a.id)}>{a.name}</a>
                                </span>
                              ))
                            : 'No actions yet'}
                        </div>
                      </div>
                      {isAdmin ? <button type="button" className="btn btn-sm2" onClick={() => navigate(`automations/new?entity=${enc(id)}&event=${enc(ev.id)}`)} aria-label={`Add an action for ${ev.label}`}>
                        <Icon name="plus" size={14} /> Add an action
                      </button> : null}
                    </div>
                  );
                })}
              </div>
            ) : null}
            {current === 'automations' ? (
              <div className="stack">
                {canWatch && isAdmin ? (
                  <button type="button" className="btn btn-sm2" style={{ alignSelf: 'flex-start' }} onClick={() => navigate(`automations/new?entity=${enc(id)}&trigger=state`)}>
                    <Icon name="bolt" size={14} /> Create automation with this device
                  </button>
                ) : null}
                {used.length ? (
                  <div className="rows">
                    {used.map((a) => (
                      <a key={a.id} className="row row--link" href={'#/automations/' + enc(a.id)}>
                        <span className="strong">{a.name}</span>
                        <span className="muted" style={{ fontSize: 13, textAlign: 'right' }}>
                          {a.summary || ''}
                        </span>
                      </a>
                    ))}
                  </div>
                ) : (
                  <div className="muted">No automations use this device yet.</div>
                )}
              </div>
            ) : null}
            {current === 'activity' ? (
              events.data === undefined ? (
                events.error ? <ErrorBox error={events.error} onRetry={events.reload} /> : <Loading />
              ) : !mine.length ? (
                <div className="muted">Nothing recent for this device.</div>
              ) : (
                <div>
                  {mine.map((x) => (
                    <ActivityItem key={x.id} event={x} />
                  ))}
                </div>
              )
            ) : null}
            {current === 'details' ? (
              <div className="stack">
                {props.length ? (
                  <dl className="attr-list" aria-label="Values it reports">
                    {props.map((p) => (
                      <div key={p.id} className="attr-row attr-row--prop">
                        <dt>{p.label}</dt>
                        <dd>
                          <span>{formatPropValue(p, p.value)}</span>
                          {isAdmin ? (
                            <button type="button" className="btn btn-sm2" onClick={() => navigate(`automations/new?entity=${enc(id)}&property=${enc(p.id)}`)} aria-label={`Create automation with ${p.label}`}>
                              <Icon name="bolt" size={13} /> Automate
                            </button>
                          ) : null}
                        </dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
                {attrs.length ? (
                  <dl className="attr-list" aria-label="About this device">
                    {attrs.map((r) => (
                      <div key={r.key} className="attr-row">
                        <dt>{r.label}</dt>
                        <dd>{r.text}</dd>
                      </div>
                    ))}
                  </dl>
                ) : null}
              </div>
            ) : null}
          </div>
        </section>
      </div>
    </div>
  );
}