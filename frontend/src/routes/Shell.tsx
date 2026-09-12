import { Link, Outlet, useLocation, useMatch, useNavigate } from 'react-router-dom';
import { useEffect, useState } from 'react';
import { GlobalNav } from '../features/workspace/GlobalNav';
import { ProjectNav } from '../features/workspace/ProjectNav';
import { WorkspaceTabs } from '../features/workspace/WorkspaceTabs';
import { describeRoute, projectIdForRoute } from '../features/workspace/workspaceRoutes';
import { WenkuCrawlProvider } from '../features/import/WenkuCrawlContext';
import { JobProgress } from '../features/jobs/JobProgress';

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
      <main style={styles.shell}>
        <GlobalNav />
        <nav style={styles.nav} aria-label="Workflow">
          <Link to="/" style={styles.navLink}>Dự án</Link>
          <Link to="/jobs" style={styles.navLink}>Jobs</Link>
          <Link to="/diagnostics" style={styles.navLink}>Diagnostics</Link>
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

const styles: Record<string, React.CSSProperties> = {
  shell: {
    minHeight: '100vh',
    boxSizing: 'border-box',
    padding: 24,
    paddingBottom: 150,
    color: '#17202a',
    background: '#f6f8fb',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  nav: {
    display: 'flex',
    // Wrap so the workflow links reflow at 320/390px and at 200% text size
    // instead of forcing a horizontal scrollbar (G-UX responsive).
    flexWrap: 'wrap',
    gap: 12,
    maxWidth: 920,
    margin: '0 auto 16px',
  },
  navLink: {
    color: '#0b5cad',
    fontWeight: 900,
    textDecoration: 'none',
  },
};
