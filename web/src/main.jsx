import React from 'react';
import { createRoot } from 'react-dom/client';

// Self-hosted fonts (latin subset only) — the dashboard must work offline. One sans face throughout.
import '@fontsource/manrope/latin-400.css';
import '@fontsource/manrope/latin-500.css';
import '@fontsource/manrope/latin-600.css';
import '@fontsource/manrope/latin-700.css';
import '@fontsource/manrope/latin-800.css';

import './styles.css';
import App from './App.jsx';
import WallApp, { isWallUrl } from './wall/WallApp.jsx';
import { startBuildWatch } from './build.js';

// A newer web build on the hub: the page reloads itself at a good moment (build.js).
startBuildWatch({ isWall: isWallUrl() });

// The wall tablet opens /#/wall (or /wall): its own screens, sign-in and look ("night glass").
createRoot(document.getElementById('root')).render(<React.StrictMode>{isWallUrl() ? <WallApp /> : <App />}</React.StrictMode>);
