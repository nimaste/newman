// Lists on the wall that scroll by touch instead of leaving things out: whether there's more past the
// end, for the soft fade on that edge (wall.css .has-more), gone once you've scrolled to the end.

import { useEffect, useLayoutEffect, useRef, useState } from 'react';

/** True while `ref`'s content goes on past its bottom edge (or its right edge, with `across`). */
export function useMoreAfter(ref, across) {
  const [more, setMore] = useState(false);
  const bound = useRef({ el: null, off: null });
  const check = () => {
    const el = ref.current;
    const m = !!el && (across ? el.scrollLeft + el.clientWidth < el.scrollWidth - 2 : el.scrollTop + el.clientHeight < el.scrollHeight - 2);
    setMore((was) => (was === m ? was : m));
  };
  const checkRef = useRef(check);
  checkRef.current = check;
  // After every draw: rows come and go, and the list itself can appear later (a tile's Look).
  useLayoutEffect(() => {
    const el = ref.current;
    if (el !== bound.current.el) {
      if (bound.current.off) bound.current.off();
      bound.current = { el, off: null };
      if (el) {
        const on = () => checkRef.current();
        el.addEventListener('scroll', on, { passive: true });
        const ro = typeof ResizeObserver !== 'undefined' ? new ResizeObserver(on) : null;
        if (ro) ro.observe(el);
        bound.current.off = () => {
          el.removeEventListener('scroll', on);
          if (ro) ro.disconnect();
        };
      }
    }
    check();
  });
  useEffect(() => () => bound.current.off && bound.current.off(), []);
  return more;
}
