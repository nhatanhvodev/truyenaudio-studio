type GateDecision = {
  allowed: boolean;
  reasons: string[];
  rights_evaluation_hash: string;
};

type ExportGateProps = {
  decision: GateDecision;
  onBuildPrivate?: () => void;
  onBuildPublication?: () => void;
};

export function ExportGate({ decision, onBuildPrivate, onBuildPublication }: ExportGateProps) {
  const blocked = decision.reasons.length > 0;

  return (
    <section aria-label="Cong xuat ban" style={styles.shell}>
      <div style={styles.header}>
        <div>
          <h2 style={styles.title}>Xuat ban</h2>
          <p style={styles.meta}>Hash danh gia quyen: {decision.rights_evaluation_hash.slice(0, 12)}</p>
        </div>
        <span style={decision.allowed ? styles.ready : styles.blocked}>{decision.allowed ? 'San sang' : 'Bi chan'}</span>
      </div>

      {blocked ? (
        <ul style={styles.reasons}>
          {decision.reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      ) : null}

      <div style={styles.actions}>
        <button type="button" style={styles.secondaryButton} onClick={onBuildPrivate}>
          Tao archive rieng tu
        </button>
        <button type="button" style={styles.primaryButton} disabled={!decision.allowed} onClick={onBuildPublication}>
          Tao goi xuat ban
        </button>
      </div>
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    color: '#17202a',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    padding: 16,
    background: '#ffffff',
  },
  header: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
  },
  title: {
    margin: 0,
    fontSize: 18,
    letterSpacing: 0,
  },
  meta: {
    margin: '6px 0 0',
    color: '#52606d',
    fontSize: 13,
  },
  ready: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#e8f5ec',
    color: '#1c6638',
    fontWeight: 700,
    fontSize: 13,
  },
  blocked: {
    borderRadius: 999,
    padding: '5px 10px',
    background: '#fff4d6',
    color: '#7a4b00',
    fontWeight: 700,
    fontSize: 13,
  },
  reasons: {
    margin: '14px 0 0',
    paddingLeft: 18,
    color: '#7a2e0e',
  },
  actions: {
    display: 'flex',
    flexWrap: 'wrap',
    gap: 10,
    marginTop: 16,
  },
  primaryButton: {
    border: '1px solid #0b5cad',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#0b5cad',
    color: '#ffffff',
    fontWeight: 700,
  },
  secondaryButton: {
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 700,
  },
};
