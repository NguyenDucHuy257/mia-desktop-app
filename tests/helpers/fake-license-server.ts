export type LicenseRequest = {
  tool: string;
  key: string;
  device_id: string;
  phone: string;
  hardware: Record<string, string>;
  legacy_keys: string[];
};

export class FakeLicenseServer {
  requests: LicenseRequest[] = [];
  private handler: (request: LicenseRequest) => Promise<Record<string, unknown>>;

  constructor(handler: (request: LicenseRequest) => Promise<Record<string, unknown>> | Record<string, unknown>) {
    this.handler = async (request) => handler(request);
  }

  respondWith(handler: (request: LicenseRequest) => Promise<Record<string, unknown>> | Record<string, unknown>) {
    this.handler = async (request) => handler(request);
  }

  async verifyKeyV2(request: LicenseRequest) {
    this.requests.push(structuredClone(request));
    return this.handler(request);
  }
}

export function activeResponse(request: LicenseRequest, overrides: Record<string, unknown> = {}) {
  return {
    valid: true,
    key: request.key,
    device_id: request.device_id,
    phone: request.phone,
    expires_at: '31/12/2028',
    expired: false,
    migrated: false,
    recovered: false,
    hardware_match: 1,
    hardware_matches: 6,
    hardware_profile: request.hardware,
    reason: 'ok',
    entitlements: { version: 1, plan: 'V', trial: false, max_tax_codes: null, allowed_tax_codes: [], date_from: null, date_to: null },
    ...overrides,
  };
}
