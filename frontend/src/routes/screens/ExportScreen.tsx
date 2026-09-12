import { Navigate, useParams } from 'react-router-dom';
import { ExportWorkflow } from '../../features/exports/ExportWorkflow';

export function ExportScreen() {
  const { chapterId } = useParams();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Xuất bản">
      {/* ExportWorkflow sở hữu tiêu đề "Xuất bản"; không render thêm heading trùng. */}
      <ExportWorkflow chapterId={chapterId} />
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
};
