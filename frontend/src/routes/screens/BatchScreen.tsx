import { Navigate, useParams } from 'react-router-dom';
import { BatchQueue } from '../../features/batch/BatchQueue';

export function BatchScreen() {
  const { projectId } = useParams();
  if (!projectId) {
    return <Navigate to="/" replace />;
  }
  // BatchQueue already posts to the per-job cancel/retry routes itself (the routes
  // were the missing half until U07) and shows the refusal reason inline, so it is
  // deliberately NOT rewired through jobActions: doing so would swallow the reason
  // behind a silent snapshot refresh.
  return <BatchQueue projectId={projectId} />;
}
