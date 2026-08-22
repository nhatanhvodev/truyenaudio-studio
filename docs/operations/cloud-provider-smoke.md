# Cloud provider paid smoke

This manual smoke is intentionally one segment per provider. Do not run it until the provider profile, policy snapshot, cloud consent, rate card, quota, and budget authorization are configured.

Required gate:

- `-AllowPaid`
- `-Provider google|gemini|eleven`
- `-AuthorizationId <uuid>`
- `-CloudConsentId <uuid>`
- `-ExpectedMaxVnd <int>` no higher than 5,000 VND
- interactive confirmation: `RUN <provider>`

Expected report fields: provider, authorization id, consent id, expected max VND, text chars, status, and redacted note. Never paste or commit API keys. Disable a profile by setting `provider_profiles.enabled=false`; consent revocation blocks new cloud calls but does not delete old local artifacts.

Provider error mapping: 401/403 => `PROVIDER_AUTH`, 429 => `PROVIDER_RATE_LIMIT`, provider quota => `PROVIDER_QUOTA`, timeout/connect => `PROVIDER_NETWORK`, malformed payload => `PROVIDER_SCHEMA`, request sent with unknown result => `BILLING_UNKNOWN`.
