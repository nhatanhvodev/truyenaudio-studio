import { useMemo, useState } from 'react';
import styles from './RoleAssignment.module.css';

export type VoiceRole = {
  id: string;
  roleKey: string;
  displayName: string;
  voicePresetId: string;
  isNarrator: boolean;
};

export type AssignableSegment = {
  id: string;
  text: string;
  roleKey?: string | null;
};

type Props = {
  roles: VoiceRole[];
  segments: AssignableSegment[];
  onSave: (assignments: Record<string, string>) => void;
};

export default function RoleAssignment({ roles, segments, onSave }: Props) {
  const narrator = roles.find((role) => role.isNarrator);
  const [assigned, setAssigned] = useState<Record<string, string>>({});
  const tooManyRoles = roles.length > 4;
  const roleKeys = useMemo(() => new Set(roles.map((role) => role.roleKey)), [roles]);

  function selectedRole(segment: AssignableSegment): string {
    const roleKey = assigned[segment.id] ?? segment.roleKey ?? narrator?.roleKey ?? 'narrator';
    return roleKeys.has(roleKey) ? roleKey : narrator?.roleKey ?? 'narrator';
  }

  function save() {
    const explicitAssignments: Record<string, string> = {};
    for (const segment of segments) {
      const nextRole = assigned[segment.id];
      const originalRole = segment.roleKey ?? narrator?.roleKey ?? 'narrator';
      if (nextRole && nextRole !== originalRole) {
        explicitAssignments[segment.id] = nextRole;
      }
    }
    onSave(explicitAssignments);
  }

  return (
    <section className={styles.shell} aria-label="Role assignment">
      <header className={styles.header}>
        <div>
          <h2 className={styles.title}>Đa giọng có hỗ trợ</h2>
          <p className={styles.note}>Narrator mặc định; dialogue chỉ đổi vai khi bạn chọn thủ công.</p>
        </div>
        <span className={styles.badge}>Tối đa 4 role</span>
      </header>

      {tooManyRoles ? <p role="alert" className={styles.error}>Tối đa bốn role tính cả narrator.</p> : null}

      <div className={styles.roles}>
        {roles.map((role) => (
          <article
            key={role.id}
            className={[styles.roleCard, role.isNarrator ? styles.narratorCard : ''].filter(Boolean).join(' ')}
          >
            <strong>{role.displayName}</strong>
            <span>{role.roleKey}</span>
            {role.isNarrator ? <em>Narrator mặc định</em> : null}
          </article>
        ))}
      </div>

      <div className={styles.segmentList}>
        {segments.map((segment) => (
          <label key={segment.id} className={styles.segment}>
            <span className={styles.segmentText}>{segment.text}</span>
            <select
              aria-label={`Vai cho đoạn ${segment.id}`}
              value={selectedRole(segment)}
              className={styles.select}
              onChange={(event) =>
                setAssigned((current) => ({ ...current, [segment.id]: event.target.value }))
              }
            >
              {roles.map((role) => (
                <option key={role.id} value={role.roleKey}>
                  {role.displayName}
                </option>
              ))}
            </select>
          </label>
        ))}
      </div>

      <button type="button" disabled={tooManyRoles} onClick={save} className={styles.button}>
        Lưu assignment
      </button>
    </section>
  );
}
