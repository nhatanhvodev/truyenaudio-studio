import { useState } from 'react';
import ProviderSettings from '../providers/ProviderSettings';
import RoleAssignment from './RoleAssignment';
import VoiceComparison from './VoiceComparison';

type Props = {
  panelStyle?: React.CSSProperties;
  titleStyle?: React.CSSProperties;
  labelStyle?: React.CSSProperties;
  inputStyle?: React.CSSProperties;
  primaryButtonStyle?: React.CSSProperties;
  secondaryButtonStyle?: React.CSSProperties;
  successStyle?: React.CSSProperties;
};

export default function MultiVoiceCloudDemo({
  panelStyle,
  titleStyle,
  labelStyle,
  inputStyle,
  primaryButtonStyle,
  secondaryButtonStyle,
  successStyle,
}: Props) {
  const [enabled, setEnabled] = useState(false);
  const [consent, setConsent] = useState(false);
  const [roleName, setRoleName] = useState('');
  const [selectedVoice, setSelectedVoice] = useState('');
  const [renderSummary, setRenderSummary] = useState('');
  const roles = [
    { id: 'role-narrator', roleKey: 'narrator', displayName: 'Narrator', voicePresetId: 'local-1', isNarrator: true },
    ...(roleName.trim()
      ? [{ id: 'role-hero', roleKey: 'hero', displayName: roleName.trim(), voicePresetId: selectedVoice || 'google-neural2', isNarrator: false }]
      : []),
  ];
  return (
    <section style={panelStyle ?? styles.panel} aria-label="Multi voice cloud demo">
      <h1 style={titleStyle ?? styles.title}>Multi-voice cloud demo</h1>
      <button type="button" onClick={() => setEnabled(true)} style={primaryButtonStyle ?? styles.primaryButton}>
        Đa giọng có hỗ trợ
      </button>
      {enabled ? (
        <>
          <button type="button" onClick={() => setRoleName('Nữ chính')} style={secondaryButtonStyle ?? styles.secondaryButton}>
            Thêm vai
          </button>
          <label style={labelStyle ?? styles.label}>
            Tên vai
            <input value={roleName} onChange={(event) => setRoleName(event.target.value)} style={inputStyle ?? styles.input} />
          </label>
          <ProviderSettings
            provider="google"
            model="Neural2"
            region="asia-southeast1"
            policySha256={'a'.repeat(64)}
            quotaLabel="1.000 ký tự miễn phí trong tháng"
            committedVnd={0}
            availableVnd={500000}
            onGrantFakeConsent={() => setConsent(true)}
          />
          <VoiceComparison
            sampleText="Nàng khẽ nói rằng mình sẽ quay lại sau khi trời sáng, còn người kể giữ nhịp chậm để người nghe không bỏ lỡ bối cảnh."
            consentGranted={consent}
            voices={[
              {
                id: 'google-neural2',
                provider: 'google',
                model: 'Neural2',
                region: 'asia-southeast1',
                license: 'policy snapshot',
                costTier: 'paid',
                online: true,
                quotaLabel: '1.000 ký tự miễn phí',
              },
            ]}
            onSelect={setSelectedVoice}
          />
          <RoleAssignment
            roles={roles}
            segments={[
              { id: 'seg-1', text: 'Người kể mở đầu cảnh.', roleKey: 'narrator' },
              { id: 'seg-2', text: 'Ta sẽ quay lại.', roleKey: 'narrator' },
              { id: 'seg-3', text: 'Người kể kết cảnh.', roleKey: 'narrator' },
            ]}
            onSave={() => setRenderSummary('Tái sử dụng 2 đoạn; render lại 1 đoạn')}
          />
          <button type="button" onClick={() => setRenderSummary('Tái sử dụng 2 đoạn; render lại 1 đoạn')} style={primaryButtonStyle ?? styles.primaryButton}>
            Đổi giọng hero và render
          </button>
          {renderSummary ? <p role="status" style={successStyle ?? styles.success}>{renderSummary}</p> : null}
        </>
      ) : null}
    </section>
  );
}

const styles: Record<string, React.CSSProperties> = {
  panel: {
    display: 'grid',
    gap: 16,
    maxWidth: 920,
    margin: '0 auto',
    padding: 20,
    border: '1px solid #d7dde8',
    borderRadius: 8,
    background: '#ffffff',
  },
  title: {
    margin: 0,
    fontSize: 26,
    letterSpacing: 0,
  },
  label: {
    display: 'grid',
    gap: 8,
    fontWeight: 900,
  },
  input: {
    width: '100%',
    minHeight: 40,
    boxSizing: 'border-box',
    padding: '8px 10px',
    border: '1px solid #c9d3df',
    borderRadius: 6,
    font: 'inherit',
  },
  primaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: 0,
    borderRadius: 6,
    background: '#155eef',
    color: '#ffffff',
    fontWeight: 900,
  },
  secondaryButton: {
    justifySelf: 'start',
    padding: '10px 14px',
    border: '1px solid #a9b7c6',
    borderRadius: 6,
    background: '#ffffff',
    color: '#17324d',
    fontWeight: 900,
  },
  success: {
    margin: 0,
    color: '#166534',
    fontWeight: 900,
  },
};
