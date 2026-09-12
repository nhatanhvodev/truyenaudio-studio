/*
 * NOT DEAD BY ACCIDENT — DO NOT DELETE, AND DO NOT RESTYLE.
 *
 * This component has zero importers (`git log -S"RightsEditor"` shows it has
 * never been imported by anything, not even the commit that added it), and it is
 * the only file under `src/` still carrying raw colour literals instead of the
 * ADR-0002 tokens. It is therefore the one documented exception to the
 * "one colour system" gate, alongside `styles/tokens.css`.
 *
 * Both facts are known and recorded, not overlooked:
 * `docs/research/product-validation-research-v2.md:142` tracks it as a product
 * GAP — "Frontend có `RightsEditor` và `CloudConsent` component nhưng route chính
 * chưa cho thấy một onboarding rights flow hoàn chỉnh". It is waiting to be wired
 * into that flow, so deleting it would throw away intended work.
 *
 * Restyling it onto tokens would be the worst of the three options: it is
 * unreachable, so the change would have no user-visible effect and no test could
 * cover it. When it is finally wired up, convert it then — in the same change
 * that makes it reachable, where a test can see the result.
 */

type Evidence = {
  id: string;
  evidence_kind: string;
  display_name: string;
  sha256: string;
  expires_at?: string | null;
};

type Grant = {
  id: string;
  scope: string;
  territory: string;
  allows_ai_processing: boolean;
  allows_third_party_cloud: boolean;
  expires_at?: string | null;
  evidence_id?: string | null;
};

type RightsEditorProps = {
  evidence: Evidence[];
  grants: Grant[];
};

export function RightsEditor({ evidence, grants }: RightsEditorProps) {
  const evidenceById = new Map(evidence.map((item) => [item.id, item]));

  return (
    <section aria-label="Quyen va bang chung" style={styles.shell}>
      <div style={styles.toolbar}>
        <h2 style={styles.title}>Quyen xuat ban</h2>
        <button type="button" style={styles.button}>
          Tai bang chung
        </button>
      </div>

      <div style={styles.grid}>
        <section aria-label="Bang chung quyen" style={styles.panel}>
          <h3 style={styles.panelTitle}>Bang chung</h3>
          {evidence.map((item) => (
            <article key={item.id} style={styles.row}>
              <div>
                <strong>{item.display_name}</strong>
                <p style={styles.meta}>{item.evidence_kind}</p>
              </div>
              <div style={styles.hash}>
                <span>Het han: {formatDate(item.expires_at)}</span>
                <code>{item.sha256.slice(0, 12)}</code>
              </div>
            </article>
          ))}
        </section>

        <section aria-label="Pham vi cap quyen" style={styles.panel}>
          <h3 style={styles.panelTitle}>Pham vi</h3>
          {grants.map((grant) => {
            const linkedEvidence = grant.evidence_id ? evidenceById.get(grant.evidence_id) : undefined;
            return (
              <article key={grant.id} style={styles.row}>
                <div>
                  <strong>
                    {grant.scope} - {grant.territory}
                  </strong>
                  <p style={styles.meta}>Het han: {formatDate(grant.expires_at)}</p>
                  <p style={styles.meta}>Bang chung: {linkedEvidence?.display_name ?? 'Chua gan'}</p>
                </div>
                <div style={styles.checks} aria-label="Quyen xu ly">
                  <label style={styles.check}>
                    <input type="checkbox" checked={grant.allows_ai_processing} readOnly />
                    AI
                  </label>
                  <label style={styles.check}>
                    <input type="checkbox" checked={grant.allows_third_party_cloud} readOnly />
                    Cloud ben thu ba
                  </label>
                </div>
              </article>
            );
          })}
        </section>
      </div>
    </section>
  );
}

function formatDate(value?: string | null) {
  if (!value) {
    return 'Khong thoi han';
  }
  return value.slice(0, 10);
}

const styles: Record<string, React.CSSProperties> = {
  shell: {
    color: '#17202a',
    fontFamily: 'Inter, Segoe UI, Arial, sans-serif',
  },
  toolbar: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginBottom: 16,
  },
  title: {
    margin: 0,
    fontSize: 22,
    letterSpacing: 0,
  },
  button: {
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    padding: '8px 12px',
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 700,
  },
  grid: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(280px, 1fr))',
    gap: 16,
  },
  panel: {
    border: '1px solid #d9e1e8',
    borderRadius: 8,
    padding: 16,
    background: '#ffffff',
  },
  panelTitle: {
    margin: '0 0 12px',
    fontSize: 16,
    letterSpacing: 0,
  },
  row: {
    display: 'flex',
    justifyContent: 'space-between',
    gap: 12,
    padding: '12px 0',
    borderTop: '1px solid #edf1f5',
  },
  meta: {
    margin: '4px 0 0',
    color: '#52606d',
    fontSize: 13,
  },
  hash: {
    display: 'grid',
    justifyItems: 'end',
    gap: 4,
    color: '#52606d',
    fontSize: 13,
  },
  checks: {
    display: 'grid',
    gap: 6,
    alignContent: 'start',
    minWidth: 144,
  },
  check: {
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    fontSize: 13,
  },
};
