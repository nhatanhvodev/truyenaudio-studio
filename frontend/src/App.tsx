import { RouterProvider } from 'react-router-dom';
import { useEffect } from 'react';
import { router } from './routes/router';

export default function App() {
  useEffect(() => {
    // Discard the legacy persisted credential without reading or reusing it.
    window.localStorage.removeItem('gemini_api_key');
  }, []);

  return <RouterProvider router={router} />;
}
