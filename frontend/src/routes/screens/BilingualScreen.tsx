import { Navigate, useNavigate, useParams } from 'react-router-dom';
import BilingualEditor from '../../features/translation/BilingualEditor';

export function BilingualScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section style={styles.panel} aria-label="Editor song ngữ">
      <BilingualEditor
        chapterId={chapterId}
        onApproved={() => navigate(`/chapters/${chapterId}/voice`)}
      />
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
