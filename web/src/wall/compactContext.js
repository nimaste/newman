// Inside the wall's compact layout (Compact.jsx): the hub's tile catalog ({ types }), else null. Tiles
// that draw themselves differently there (the camera, the dial, the weather) ask for it.

import { createContext, useContext } from 'react';

export const CompactContext = createContext(null);

export function useCompactWall() {
  return useContext(CompactContext);
}
