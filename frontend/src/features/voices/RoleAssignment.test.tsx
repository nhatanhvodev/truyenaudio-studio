import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import RoleAssignment from './RoleAssignment';

afterEach(() => {
  cleanup();
});

describe('RoleAssignment', () => {
  it('keeps unresolved segments on narrator and submits explicit assignments', () => {
    const onSave = vi.fn();
    render(
      <RoleAssignment
        roles={[
          { id: 'role-narrator', roleKey: 'narrator', displayName: 'Narrator', voicePresetId: 'voice-1', isNarrator: true },
          { id: 'role-hero', roleKey: 'hero', displayName: 'Hero', voicePresetId: 'voice-2', isNarrator: false },
        ]}
        segments={[
          { id: 'seg-1', text: 'Loi dan truyen', roleKey: 'narrator' },
          { id: 'seg-2', text: 'Ta se quay lai.', roleKey: 'narrator' },
        ]}
        onSave={onSave}
      />,
    );

    expect(screen.getByText('Narrator mặc định')).toBeVisible();
    fireEvent.change(screen.getByLabelText('Vai cho đoạn seg-2'), { target: { value: 'hero' } });
    fireEvent.click(screen.getByRole('button', { name: 'Lưu assignment' }));

    expect(onSave).toHaveBeenCalledWith({ 'seg-2': 'hero' });
  });

  it('blocks more than four roles', () => {
    render(
      <RoleAssignment
        roles={[
          { id: '1', roleKey: 'narrator', displayName: 'Narrator', voicePresetId: 'voice-1', isNarrator: true },
          { id: '2', roleKey: 'a', displayName: 'A', voicePresetId: 'voice-2', isNarrator: false },
          { id: '3', roleKey: 'b', displayName: 'B', voicePresetId: 'voice-3', isNarrator: false },
          { id: '4', roleKey: 'c', displayName: 'C', voicePresetId: 'voice-4', isNarrator: false },
          { id: '5', roleKey: 'd', displayName: 'D', voicePresetId: 'voice-5', isNarrator: false },
        ]}
        segments={[]}
        onSave={() => undefined}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Tối đa bốn role');
    expect(screen.getByRole('button', { name: 'Lưu assignment' })).toBeDisabled();
  });
});
