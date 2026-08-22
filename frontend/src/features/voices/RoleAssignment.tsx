import { useMemo, useState } from 'react';

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
    <section style={styles.shell} aria-label="Role assignment">
      <header style={styles.header}>
        <div>
          <h2 style={styles.title}>Đa giọng có hỗ trợ</h2>
          <p style={styles.note}>Narrator mặc định; dialogue chỉ đổi vai khi bạn chọn thủ công.</p>
        </div>
        <span style={styles.badge}>Tối đa 4 role</span>
      </header>

      {tooManyRoles ? <p role="alert" style={styles.error}>Tối đa bốn role tính cả narrator.</p> : null}

      <div style={styles.roles}>
        {roles.map((role) => (
          <article key={role.id} style={role.isNarrator ? styles.narratorCard : styles.roleCard}>
            <strong>{role.displayName}</strong>
            <span>{role.roleKey}</span>
            {role.isNarrator ? <em>Narrator mặc định</em> : null}
          </article>
        ))}
      </div>

      <div style={styles.segmentList}>
        {segments.map((segment) => (
          <label key={segment.id} style={styles.segment}>
            <span style={styles.segmentText}>{segment.text}</span>
            <select
              aria-label={`Vai cho đoạn ${segment.id}`}
              value={selectedRole(segment)}
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

      <button type="button" disabled={tooManyRoles} onClick={save} style={styles.button}>
        Lưu assignment
      </button>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    display: 'grid',
    gap: 14,
    padding: 16,
    border: '1px solid #d9e1e8',
    borderRadius: 10,
    background: '#fff',
    color: '#17202a',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 12,
    alignItems: 'start',
  },
  title: {
    margin: 0,
    fontSize: 20,
  },
  note: {
    margin: '4px 0 0',
    color: '#52606d',
  },
  badge: {
    padding: '5px 8px',
    borderRadius: 999,
    background: '#eef2ff',
    color: '#3730a3',
    fontWeight: 700,
  },
  error: {
    margin: 0,
    color: '#b42318',
    fontWeight: 700,
  },
  roles: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: 8,
  },
  roleCard: {
    display: 'grid',
    gap: 3,
    padding: 10,
    border: '1px solid #d9e1e8',
    borderRadius: 8,
  },
  narratorCard: {
    display: 'grid',
    gap: 3,
    padding: 10,
    border: '1px solid #99c2ff',
    borderRadius: 8,
    background: '#f0f7ff',
  },
  segmentList: {
    display: 'grid',
    gap: 8,
  },
  segment: {
    display: 'grid',
    gridTemplateColumns: '1fr minmax(150px, 220px)',
    gap: 10,
    alignItems: 'center',
  },
  segmentText: {
    color: '#2f3a45',
  },
  button: {
    justifySelf: 'start',
    padding: '9px 14px',
    borderRadius: 8,
    border: 0,
    background: '#1f6feb',
    color: '#fff',
    fontWeight: 800,
  },
};
