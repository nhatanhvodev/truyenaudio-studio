import { RouterProvider } from 'react-router-dom';
import { useEffect } from 'react';

import { ThemeProvider } from './features/settings/ThemeProvider';
import { router } from './routes/router';

export default function App() {
  useEffect(() => {
    // Discard the legacy persisted credential without reading or reusing it.
    window.localStorage.removeItem('gemini_api_key');
  }, []);

  return (
    <ThemeProvider>
      <RouterProvider router={router} />
    </ThemeProvider>
  );
}
