import { Navigate, useNavigate, useParams } from 'react-router-dom';
import BilingualEditor from '../../features/translation/BilingualEditor';
import styles from './BilingualScreen.module.css';


export function BilingualScreen() {
  const { chapterId } = useParams();
  const navigate = useNavigate();
  if (!chapterId) {
    return <Navigate to="/" replace />;
  }
  return (
    <section className={styles.panel} aria-label="Editor song ngữ">
      <BilingualEditor
        chapterId={chapterId}
        onApproved={() => navigate(`/chapters/${chapterId}/voice`)}
      />
    </section>
  );
}
