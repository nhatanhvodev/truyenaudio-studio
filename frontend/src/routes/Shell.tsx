import { Link, Outlet, useLocation, useMatch, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { GlobalNav } from '../features/workspace/GlobalNav';
import { ProjectNav } from '../features/workspace/ProjectNav';
import { WorkspaceTabs } from '../features/workspace/WorkspaceTabs';
import { describeRoute, projectIdForRoute } from '../features/workspace/workspaceRoutes';
import { WenkuCrawlProvider } from '../features/import/WenkuCrawlContext';
import { JobProgress } from '../features/jobs/JobProgress';

import styles from './Shell.module.css';

export function Shell() {
  const location = useLocation();
  const navigate = useNavigate();
  // The project id comes from the URL, so deep links and Back always stay inside
  // the same project (U02); the shell only needs the match, not the child route.
  //
  // Both hooks must be called on EVERY render: `useMatch(a) ?? useMatch(b)`
  // short-circuits, so navigating from a project route to a chapter route changed
  // the hook count mid-app and crashed React ("Cannot read properties of undefined
  // (reading 'length')" inside areHookInputsEqual). Found by the U03 browser E2E.
  const projectWildcardMatch = useMatch('/projects/:projectId/*');
  const projectExactMatch = useMatch('/projects/:projectId');
  const projectId =
    projectWildcardMatch?.params.projectId ?? projectExactMatch?.params.projectId ?? null;
  const [visited, setVisited] = useState<string[]>([location.pathname]);

  useEffect(() => {
    setVisited((current) =>
      current.includes(location.pathname) ? current : [...current, location.pathname],
    );
  }, [location.pathname]);

  const openTabs = visited.map(describeRoute);
  const currentTabId = openTabs.some((tab) => tab.id === location.pathname) ? location.pathname : null;
  const layoutProjectId = projectIdForRoute(location.pathname) ?? 'local';

  return (
    <WenkuCrawlProvider>
      <main className={styles.shell}>
        <GlobalNav />
        <nav className={styles.nav} aria-label="Workflow">
          <Link to="/" className={styles.navLink}>Dự án</Link>
          <Link to="/jobs" className={styles.navLink}>Jobs</Link>
          <Link to="/diagnostics" className={styles.navLink}>Diagnostics</Link>
        </nav>
        {projectId ? <ProjectNav projectId={projectId} /> : null}
        <WorkspaceTabs
          projectId={layoutProjectId}
          openTabs={openTabs}
          currentTabId={currentTabId}
          onNavigate={(to) => navigate(to)}
        />
        <Outlet />
        <JobProgress />
      </main>
    </WenkuCrawlProvider>
  );
}

