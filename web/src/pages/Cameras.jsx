import { useState } from 'react';
import { useHub } from '../hooks.js';
import { ErrorBox, Loading, Tabs } from '../components/Controls.jsx';
import CameraGrid, { cameraTiles } from '../components/CameraGrid.jsx';
import { AlertList } from '../components/AlertCard.jsx';

/** Cameras: all cameras as live video (tap one for the full-screen view), and the latest alerts. */
export default function Cameras() {
  const { entities, entitiesError, reloadEntities, kindsById } = useHub();
  const [tab, setTab] = useState('live');
  const hasCams = !!entities && cameraTiles(entities, kindsById).length > 0;
  return (
    <div className="app-page">
      <div className="app-head">
        <h1 className="page-title">Cameras</h1>
        {hasCams ? (
          <Tabs
            label="Cameras"
            value={tab}
            onChange={setTab}
            tabs={[
              { id: 'live', label: 'All cameras' },
              { id: 'alerts', label: 'Latest alerts' },
            ]}
          />
        ) : null}
        {hasCams && tab === 'live' ? <span className="muted head-hint">Tap one to watch it full screen</span> : null}
      </div>
      <div className="pane pane--plain">
        <div className="pane-body pane-body--flush-x">
          {!entities ? (
            entitiesError ? <ErrorBox error={entitiesError} onRetry={reloadEntities} /> : <Loading>Loading cameras…</Loading>
          ) : tab === 'alerts' && hasCams ? (
            <AlertList path="api/alerts" grid />
          ) : (
            <CameraGrid />
          )}
        </div>
      </div>
    </div>
  );
}
