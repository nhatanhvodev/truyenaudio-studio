import { Navigate, useParams } from 'react-router-dom';
import { ExportWorkflow } from '../../features/exports/ExportWorkflow';

import styles from './ExportScreen.module.css';

export function ExportScreen() {
  const { chapterId } = useParams();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section className={styles.panel} aria-label="Xuất bản">
      {/* ExportWorkflow sở hữu tiêu đề "Xuất bản"; không render thêm heading trùng. */}
      <ExportWorkflow chapterId={chapterId} />
    </section>
  );
}
