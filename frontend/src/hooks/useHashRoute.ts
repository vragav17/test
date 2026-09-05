import { useEffect, useState } from 'react';

export type Route = { name: 'new' } | { name: 'job'; id: string };

/**
 * Two views is not worth a router dependency. A job gets its own URL
 * (`#job/<id>`) so it is linkable and the back button works.
 */
export function useHashRoute(): Route {
  const [hash, setHash] = useState(() => window.location.hash);

  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
  }, []);

  const match = /^#job\/([a-z0-9]+)$/.exec(hash);
  return match ? { name: 'job', id: match[1] } : { name: 'new' };
}

export function goToJob(id: string) {
  window.location.hash = `#job/${id}`;
}

export function goToNew() {
  window.location.hash = '';
}
