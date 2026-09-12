import { useState } from 'react';
import ProviderSettings from '../providers/ProviderSettings';
import RoleAssignment from './RoleAssignment';
import VoiceComparison from './VoiceComparison';
import styles from './MultiVoiceCloudDemo.module.css';

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
    <section className={styles.panel} style={panelStyle} aria-label="Multi voice cloud demo">
      <h1 className={styles.title} style={titleStyle}>Multi-voice cloud demo</h1>
      <button
        type="button"
        onClick={() => setEnabled(true)}
        className={styles.primaryButton}
        style={primaryButtonStyle}
      >
        Đa giọng có hỗ trợ
      </button>
      {enabled ? (
        <>
          <button
            type="button"
            onClick={() => setRoleName('Nữ chính')}
            className={styles.secondaryButton}
            style={secondaryButtonStyle}
          >
            Thêm vai
          </button>
          <label className={styles.label} style={labelStyle}>
            Tên vai
            <input
              value={roleName}
              onChange={(event) => setRoleName(event.target.value)}
              className={styles.input}
              style={inputStyle}
            />
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
          <button
            type="button"
            onClick={() => setRenderSummary('Tái sử dụng 2 đoạn; render lại 1 đoạn')}
            className={styles.primaryButton}
            style={primaryButtonStyle}
          >
            Đổi giọng hero và render
          </button>
          {renderSummary ? (
            <p role="status" className={styles.success} style={successStyle}>{renderSummary}</p>
          ) : null}
        </>
      ) : null}
    </section>
  );
}
