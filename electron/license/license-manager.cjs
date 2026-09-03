const crypto = require('node:crypto');
const { buildLegacyDetection, normalizePhone, readLegacyPhones } = require('./legacy-detector.cjs');
const { isLicenseAccessGranted } = require('./access-control.cjs');

const TOOL = 'MIA';
const ACTIVE_STATES = new Set(['active']);
const SUCCESS_REASONS = new Set([
  'ok',
  'interim_v2_upgraded',
  'recovered_existing_device',
  'legacy_already_migrated',
  'legacy_migrated',
]);

function miaV2Key(deviceId, phone) {
  const normalizedPhone = normalizePhone(phone);
  if (!normalizedPhone) return null;
  const digest = crypto.createHash('sha256').update(`${TOOL}|${deviceId}`, 'utf8').digest('hex');
  return `KEYV2-${digest.slice(0, 32)}-${normalizedPhone}`;
}

function maskPhone(phone) {
  const value = normalizePhone(phone);
  return value ? `${value.slice(0, 3)}****${value.slice(-3)}` : null;
}

function maskKey(key) {
  const value = String(key || '');
  return value.length > 12 ? `${value.slice(0, 8)}****${value.slice(-4)}` : value || null;
}

function newProfile(evidence, phone = null, deviceId = crypto.randomUUID()) {
  return {
    version: 3,
    device_id: deviceId,
    phone: normalizePhone(phone),
    hardware: { ...evidence.hardware },
  };
}

function safeState(state, details = {}) {
  return Object.freeze({ state, active: ACTIVE_STATES.has(state), ...details });
}

function responseGrantsLicense(response) {
  return response?.valid === true
    && response?.expired === false
    && SUCCESS_REASONS.has(String(response?.reason || ''));
}

function responseDiagnostic(response) {
  return {
    valid: response?.valid === true,
    expired: response?.expired === true,
    reason: typeof response?.reason === 'string' ? response.reason : null,
    migrated: response?.migrated === true,
    recovered: response?.recovered === true,
    has_key: typeof response?.key === 'string' && response.key.length > 0,
    has_device_id: typeof response?.device_id === 'string' && response.device_id.length > 0,
  };
}

class LicenseManager {
  constructor({ enabled, store, api, securityDirectory, ensureIdentity, collectEvidence, logger, now = () => new Date(), legacyPhonePaths = [], createDeviceId = () => crypto.randomUUID() }) {
    this.enabled = Boolean(enabled);
    this.store = store;
    this.api = api;
    this.securityDirectory = securityDirectory;
    this.ensureIdentity = ensureIdentity;
    this.collectEvidence = collectEvidence;
    this.logger = logger;
    this.now = now;
    this.legacyPhonePaths = legacyPhonePaths;
    this.createDeviceId = createDeviceId;
    this.current = this.enabled
      ? safeState('checking', { valid: false, expired: false })
      : safeState('error', { valid: false, expired: false, reason: 'license_disabled', mode: 'disabled' });
    this.inFlight = null;
    this.profile = null;
    this.identity = null;
    this.evidence = null;
    this.detection = null;
  }

  log(event, fields = {}) { this.logger?.info?.(event, fields); }
  status() { return this.current; }

  details() {
    const details = this.current.details || {};
    let saved = null;
    try { saved = this.store.loadLicense(); } catch { saved = null; }
    return {
      state: this.current.state,
      active: this.current.active,
      phone: maskPhone(details.phone),
      phone_status: details.phone_status || null,
      expires_at: details.expires_at || null,
      device_bound: Boolean(details.device_id),
      canonical_key: maskKey(saved?.canonical_key || details.canonical_key),
      reason: this.current.reason || null,
      mode: this.current.mode || null,
    };
  }

  revealKey() {
    const saved = this.store.loadLicense();
    return typeof saved?.canonical_key === 'string' ? saved.canonical_key : null;
  }

  async prepareLocalState() {
    // Preserve MIA's safeStorage-protected Ed25519 identity. The shared
    // Taxsoft endpoint itself does not require a separate challenge service.
    this.identity = this.ensureIdentity();
    this.evidence = await this.collectEvidence();
    let profile = null;
    try { profile = this.store.loadProfile(); } catch (error) {
      this.log('license_profile_corrupt', { code: error.code });
      throw error;
    }
    const userDataDirectory = this.securityDirectory.replace(/[\\/]security$/, '');
    const legacyPhones = readLegacyPhones(userDataDirectory, this.legacyPhonePaths);
    this.profile = profile && typeof profile.device_id === 'string'
      ? { ...profile, version: 3, hardware: profile.hardware || this.evidence.hardware }
      : newProfile(this.evidence, legacyPhones[0], this.createDeviceId());
    this.store.saveProfile(this.profile);
    this.detection = buildLegacyDetection(this.evidence, [this.profile.phone, ...legacyPhones]);
    return this.detection;
  }

  requestPayload({ phone = this.profile.phone, legacyKeys = [] } = {}) {
    const normalizedPhone = normalizePhone(phone);
    if (!normalizedPhone) {
      throw Object.assign(new Error('phone required'), { code: 'phone_required' });
    }
    return {
      tool: TOOL,
      key: miaV2Key(this.profile.device_id, normalizedPhone),
      device_id: this.profile.device_id,
      phone: normalizedPhone,
      hardware: this.evidence.hardware,
      legacy_keys: [...new Set(legacyKeys)].slice(0, 32),
    };
  }

  persistActive(response, source) {
    const candidate = {
      state: 'active', active: true, valid: response?.valid,
      expired: response?.expired, reason: responseGrantsLicense(response) ? 'ok' : response?.reason,
    };
    if (!responseGrantsLicense(response) || !isLicenseAccessGranted(candidate) || !response?.key || !response?.device_id) {
      throw Object.assign(new Error('active license response is incomplete'), { code: 'invalid_response' });
    }
    const phone = normalizePhone(response.phone) || this.profile.phone || null;
    this.profile = {
      ...this.profile,
      version: 3,
      device_id: response.device_id,
      phone,
      hardware: Object.keys(response.hardware_profile || {}).length >= 3
        ? { ...response.hardware_profile }
        : { ...this.evidence.hardware },
    };
    this.store.saveProfile(this.profile);
    this.store.saveLicense({
      version: 2,
      canonical_key: response.key,
      device_id: response.device_id,
      phone,
      phone_status: response.phone_status || (phone ? 'verified' : 'pending'),
      expires_at: response.expires_at || null,
      last_verified_at: this.now().toISOString(),
      source,
    });
    this.store.saveMigrationState({ status: 'completed', reason: source, updated_at: this.now().toISOString() });
    this.current = safeState('active', {
      valid: true,
      expired: false,
      reason: 'ok',
      details: {
        device_id: response.device_id,
        phone,
        phone_status: response.phone_status || (phone ? 'verified' : 'pending'),
        expires_at: response.expires_at || null,
      },
    });
    return this.current;
  }

  stateForRejected(response, { phone = null } = {}) {
    const reason = String(response?.reason || 'license_verify_failed');
    if (response?.expired || ['expired', 'legacy_key_expired'].includes(reason)) {
      return safeState('expired', { reason, details: { expires_at: response?.expires_at || null } });
    }
    if (reason === 'hardware_mismatch_below_50_percent' || reason === 'recovery_ambiguous') {
      return safeState('verification_required', { reason });
    }
    if (!normalizePhone(phone) && ['phone_required', 'no_legacy_match', 'key_not_activated'].includes(reason)) {
      return safeState('phone_required', { reason });
    }
    if (reason === 'key_not_activated') {
      return safeState('activation_required', {
        reason,
        activation_key: response?.key || miaV2Key(this.profile.device_id, phone),
        phone: maskPhone(phone),
      });
    }
    return safeState('error', { reason });
  }

  initialize() {
    if (!this.enabled) return Promise.resolve(this.current);
    if (this.inFlight) return this.inFlight;
    this.inFlight = this.initializeOnce().finally(() => { this.inFlight = null; });
    return this.inFlight;
  }

  async initializeOnce() {
    this.current = safeState('checking');
    this.log('license_init_started');
    try {
      const detection = await this.prepareLocalState();
      let saved;
      try { saved = this.store.loadLicense(); } catch (error) {
        this.current = safeState('error', { reason: error.code || 'license_state_corrupt' });
        return this.current;
      }
      const phone = normalizePhone(saved?.phone)
        || normalizePhone(this.profile?.phone)
        || detection.phones[0]
        || null;
      this.log('legacy_detection_completed', {
        candidates: detection.exact_key_candidates.length,
        schemas: detection.detected_schema.length,
      });
      if (!phone) {
        this.current = safeState(detection.has_any_candidate ? 'legacy_phone_required' : 'phone_required');
        return this.current;
      }

      if (this.profile.phone !== phone) {
        this.profile = { ...this.profile, phone };
        this.store.saveProfile(this.profile);
      }
      this.detection = buildLegacyDetection(this.evidence, [phone, ...detection.phones]);
      if (!saved?.canonical_key && this.detection.has_any_candidate) this.current = safeState('migrating');
      const response = await this.api.verifyKeyV2(this.requestPayload({
        phone,
        legacyKeys: this.detection.exact_key_candidates,
      }));
      this.log('license_verify_response', responseDiagnostic(response));
      if (responseGrantsLicense(response)) {
        const source = response.migrated
          ? 'legacy_migration'
          : response.recovered ? 'device_recovery' : 'v2_verify';
        if (response.migrated) this.log('legacy_migration_success', { migrated: true });
        return this.persistActive(response, source);
      }
      this.current = response?.valid === true
        ? safeState('error', { valid: false, expired: Boolean(response?.expired), reason: 'invalid_response' })
        : this.stateForRejected(response, { phone });
      return this.current;
    } catch (error) {
      const reason = error.code || 'license_initialize_failed';
      this.log('license_init_failed', {
        code: reason,
        error_type: error?.name || 'Error',
        status: Number.isInteger(error?.status) ? error.status : null,
        transient: Boolean(error?.transient),
      });
      this.current = safeState('error', { reason });
      return this.current;
    }
  }

  async submitPhone(value) {
    if (!this.enabled) return this.current;
    const phone = normalizePhone(value);
    if (!phone) throw Object.assign(new Error('invalid phone'), { code: 'invalid_phone' });
    try {
      if (!this.identity || !this.evidence || !this.profile) await this.prepareLocalState();
      this.profile = { ...this.profile, phone };
      this.store.saveProfile(this.profile);
      this.detection = buildLegacyDetection(this.evidence, [phone]);
      const response = await this.api.verifyKeyV2(this.requestPayload({
        phone,
        legacyKeys: this.detection.exact_key_candidates,
      }));
      this.log('license_verify_response', responseDiagnostic(response));
      this.current = responseGrantsLicense(response)
        ? this.persistActive(response, response.migrated ? 'legacy_migration' : response.recovered ? 'device_recovery' : 'v2_verify')
        : response?.valid === true
          ? safeState('error', { valid: false, expired: Boolean(response?.expired), reason: 'invalid_response' })
          : this.stateForRejected(response, { phone });
      return this.current;
    } catch (error) {
      const reason = error.code || 'license_request_failed';
      this.log('license_submit_failed', { code: reason, error_type: error?.name || 'Error' });
      this.current = safeState('error', { valid: false, expired: false, reason });
      return this.current;
    }
  }

  updatePhone(value) { return this.submitPhone(value); }

  retry() {
    if (this.current.state === 'activation_required' && this.profile?.phone) return this.submitPhone(this.profile.phone);
    return this.initialize();
  }
}

module.exports = { LicenseManager, maskKey, maskPhone, miaV2Key, newProfile, responseGrantsLicense };
