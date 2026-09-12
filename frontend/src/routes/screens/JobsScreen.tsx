import JobsList from '../../features/jobs/JobsList';
import { JobProgress } from '../../features/jobs/JobProgress';
import { jobActions } from '../jobActions';

export function JobsScreen() {
  const { onCancel, onRetry } = jobActions();
  return (
    <section style={styles.panel}>
      <h1 style={styles.title}>Jobs</h1>
      <JobsList onRetry={onRetry} onCancel={onCancel} />
      <JobProgress />
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'grid',
    gap: 16,
    maxWidth: 920,
    margin: '0 auto',
    padding: 20,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
};
