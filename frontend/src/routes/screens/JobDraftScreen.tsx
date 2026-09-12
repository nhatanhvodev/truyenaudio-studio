import { Navigate, useParams } from 'react-router-dom';
import JobDraftPanel from '../../features/jobs/JobDraftPanel';

export function JobDraftScreen() {  const { jobId } = useParams();
  if (!jobId) {
    return <Navigate to="/jobs" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Nháp job">
      <h1 style={styles.title}>Nháp đang dịch</h1>
      <JobDraftPanel jobId={jobId} />
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
