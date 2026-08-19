import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import App from './App';

describe('App', () => {
  it('renders studio name', () => {
    render(<App />);
    expect(screen.getByRole('heading', { name: 'Truyện Audio Studio' })).toBeVisible();
  });
});
