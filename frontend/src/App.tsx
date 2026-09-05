import { useEffect, useState } from 'react';

import { getHealth, listJobs } from './api';
import { JobView } from './components/JobView';
import { NewComparison } from './components/NewComparison';
import { Sidebar } from './components/Sidebar';
import { TopBar } from './components/TopBar';
import { useHashRoute } from './hooks/useHashRoute';
import type { Health, JobListEntry } from './types';

export default function App() {
  const route = useHashRoute();
  const [health, setHealth] = useState<Health | null>(null);
  const [jobs, setJobs] = useState<JobListEntry[]>([]);

  useEffect(() => {
    getHealth()
      .then(setHealth)
      .catch(() => setHealth(null));
  }, []);

  // The sidebar reflects work started elsewhere too, so poll it rather than
  // only refreshing on this tab's own actions.
  useEffect(() => {
    let alive = true;
    const refresh = () => {
      listJobs()
        .then((j) => alive && setJobs(j))
        .catch(() => undefined);
    };
    refresh();
    const timer = window.setInterval(refresh, 3000);
    return () => {
      alive = false;
      window.clearInterval(timer);
    };
  }, []);

  return (
    <>
      <TopBar health={health} />
      <div className="layout">
        <Sidebar jobs={jobs} activeId={route.name === 'job' ? route.id : null} />
        <main className="main">
          {route.name === 'job' ? (
            <JobView key={route.id} jobId={route.id} />
          ) : (
            <NewComparison health={health} />
          )}
        </main>
      </div>
    </>
  );
}
