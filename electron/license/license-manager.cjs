const crypto = require('node:crypto');
const { buildLegacyDetection, normalizePhone, readLegacyPhones } = require('./legacy-detector.cjs');

const TOOL = 'MIA';
const ACTIVE_STATES = new Set(['active', 'offline']);

function maskPhone(phone) {
  const value = normalizePhone(phone);
  return value ? `${value.slice(0, 3)}****${value.slice(-3)}` : null;
}

function maskKey(key) {
  const value = String(key || '');
  return value.length > 12 ? `${value.slice(0, 8)}****${value.slice(-4)}` : value || null;
}

function newProfile(evidence, phone = null) {
  return {
    version: 3,
    device_id: crypto.randomUUID(),
    phone: normalizePhone(phone),
    hardware: { ...evidence.hardware },
  };
}

function safeState(state, details = {}) {
  return Object.freeze({ state, active: ACTIVE_STATES.has(state), ...details });
}

class LicenseManager {
  constructor({
    enabled, store, api, securityDirectory, ensureIdentity, signChallenge,
    collectEvidence, logger, now = () => new Date(), legacyPhonePaths = [],
  }) {
    this.enabled = Boolean(enabled);
    this.store = store;
    this.api = api;
    this.securityDirectory = securityDirectory;
    this.ensureIdentity = ensureIdentity;
    this.signChallenge = signChallenge;
    this.collectEvidence = collectEvidence;
    this.logger = logger;
    this.now = now;
    this.legacyPhonePaths = legacyPhonePaths;
    this.current = this.enabled ? safeState('checking') : safeState('active', { mode: 'disabled' });
    this.inFlight = null;
    this.profile = null;
    this.identity = null;
    this.evidence = null;
  }

  log(event, fields = {}) {
    this.logger?.info?.(event, fields);
  }

  status() { return this.current; }

  details() {
    const details = this.current.details || {};
    return {
      state: this.current.state,
      active: this.current.active,
      phone: maskPhone(details.phone),
      phone_status: details.phone_status || null,
      expires_at: details.expires_at || null,
      device_bound: Boolean(details.device_id),
      canonical_key: maskKey(details.canonical_key),
      reason: this.current.reason || null,
      mode: this.current.mode || null,
    };
  }

  async proof(action) {
    const response = await this.api.challenge({
      tool: TOOL,
      action,
      public_key: this.identity.publicKeyPem,
      device_fingerprint: this.identity.fingerprint,
    });
    if (!response || typeof response.challenge !== 'string' || typeof response.challenge_id !== 'string') {
      const error = new Error('invalid challenge response');
      error.code = 'invalid_response';
      throw error;
    }
    return {
      tool: TOOL,
      challenge_id: response.challenge_id,
      challenge: response.challenge,
      public_key: this.identity.publicKeyPem,
      device_fingerprint: this.identity.fingerprint,
      signature: this.signChallenge(response.challenge),
    };
  }

  prepareLocalState() {
    this.identity = this.ensureIdentity();
    return this.collectEvidence().then((evidence) => {
      this.evidence = evidence;
      let profile = null;
      try { profile = this.store.loadProfile(); } catch (error) {
        this.log('license_profile_corrupt', { code: error.code });
      }
      const legacyPhones = readLegacyPhones(this.securityDirectory.replace(/[\\/]security$/, ''), this.legacyPhonePaths);
      this.profile = profile && typeof profile.device_id === 'string'
        ? { ...profile, hardware: profile.hardware || evidence.hardware }
        : newProfile(evidence, legacyPhones[0]);
      this.store.saveProfile(this.profile);
      return buildLegacyDetection(evidence, [this.profile.phone, ...legacyPhones]);
    });
  }

  async persistActive(response, source) {
    if (!response?.license_token || !response?.license_id || !response?.device_id) {
      const error = new Error('active license response is incomplete');
      error.code = 'invalid_response';
      throw error;
    }
    this.profile = {
      ...this.profile,
      version: 3,
      device_id: response.device_id,
      phone: normalizePhone(response.phone) || this.profile.phone || null,
      hardware: { ...this.evidence.hardware },
    };
    this.store.saveProfile(this.profile);
    this.store.saveLicense({
      version: 1,
      license_token: response.license_token,
      license_id: response.license_id,
      device_id: response.device_id,
      canonical_key: response.canonical_key,
      phone: this.profile.phone,
      phone_status: response.phone_status,
      expires_at: response.expires_at,
      offline_valid_until: response.offline_valid_until,
      source,
    });
    this.store.saveMigrationState({ status: 'completed', reason: source, updated_at: this.now().toISOString() });
    this.current = safeState('active', {
      details: {
        license_id: response.license_id, device_id: response.device_id,
        canonical_key: response.canonical_key, phone: this.profile.phone,
        phone_status: response.phone_status, expires_at: response.expires_at,
      },
    });
    return this.current;
  }

  offlineState(saved) {
    const until = Date.parse(String(saved?.offline_valid_until || ''));
    if (!Number.isFinite(until) || until < this.now().getTime()) return null;
    this.current = safeState('offline', {
      reason: 'offline_lease',
      details: {
        license_id: saved.license_id, device_id: saved.device_id,
        canonical_key: saved.canonical_key, phone: saved.phone,
        phone_status: saved.phone_status, expires_at: saved.expires_at,
      },
    });
    return this.current;
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
    let detection;
    try {
      detection = await this.prepareLocalState();
      let saved;
      try { saved = this.store.loadLicense(); } catch (error) {
        this.current = safeState('error', { reason: error.code || 'license_state_corrupt' });
        return this.current;
      }
      if (saved?.license_token) {
        try {
          const response = await this.api.verify({ ...(await this.proof('verify')), license_token: saved.license_token });
          if (response.valid) return this.persistActive(response, 'v2_verify');
          if (response.reason === 'expired' || response.reason === 'revoked') {
            this.current = safeState(response.reason, { reason: response.reason });
            return this.current;
          }
          this.current = safeState('error', { reason: response.reason || 'license_verify_failed' });
          return this.current;
        } catch (error) {
          if (error.code === 'device_mismatch' && normalizePhone(saved.phone)) {
            const recovered = await this.api.recover({
              ...(await this.proof('recover')),
              phone: normalizePhone(saved.phone),
              hardware: this.evidence.hardware,
            });
            if (recovered?.valid && recovered?.recovered) {
              return this.persistActive(recovered, 'device_recovery');
            }
            if (recovered?.reason === 'recovery_ambiguous') {
              this.current = safeState('verification_required', { reason: recovered.reason });
              return this.current;
            }
          }
          if (error.transient) {
            const offline = this.offlineState(saved);
            if (offline) return offline;
          }
          this.current = safeState('error', { reason: error.code || 'license_verify_failed' });
          return this.current;
        }
      }

      this.current = safeState('migrating');
      this.log('legacy_detection_completed', {
        candidates: detection.exact_key_candidates.length,
        schemas: detection.detected_schema.length,
      });
      if (detection.has_any_candidate) {
        const response = await this.api.migrate({
          ...(await this.proof('migrate')),
          key: `MIAV2-${crypto.createHash('sha256').update(`${TOOL}|${this.profile.device_id}`).digest('hex').slice(0, 32).toUpperCase()}`,
          device_id: this.profile.device_id,
          phone: detection.phones[0] || null,
          hardware: this.evidence.hardware,
          legacy_keys: detection.exact_key_candidates,
          legacy_hashes: detection.hash29_candidates,
        });
        if (response.valid && response.migrated) {
          this.log('legacy_migration_success', { source: response.migration_source });
          return this.persistActive(response, response.migration_source || 'legacy_migration');
        }
        if (response.reason === 'legacy_ambiguous') {
          this.current = safeState('verification_required', { reason: response.reason });
          return this.current;
        }
        if (response.reason !== 'no_legacy_match') {
          this.current = safeState('error', { reason: response.reason || 'legacy_migration_failed' });
          return this.current;
        }
      }
      this.current = safeState('phone_required');
      return this.current;
    } catch (error) {
      this.current = safeState('error', { reason: error.code || 'license_initialize_failed' });
      return this.current;
    }
  }

  async submitPhone(value) {
    if (!this.enabled) return this.current;
    const phone = normalizePhone(value);
    if (!phone) {
      const error = new Error('invalid phone');
      error.code = 'invalid_phone';
      throw error;
    }
    if (!this.identity || !this.evidence || !this.profile) await this.prepareLocalState();
    this.profile = { ...this.profile, phone };
    this.store.saveProfile(this.profile);
    const recovered = await this.api.recover({
      ...(await this.proof('recover')),
      phone,
      hardware: this.evidence.hardware,
    });
    if (recovered?.valid && recovered?.recovered) {
      return this.persistActive(recovered, 'device_recovery');
    }
    if (recovered?.reason === 'recovery_ambiguous') {
      this.current = safeState('verification_required', { reason: recovered.reason });
      return this.current;
    }
    const response = await this.api.activate({
      ...(await this.proof('activate')),
      key: `MIAV2-${crypto.createHash('sha256').update(`${TOOL}|${this.profile.device_id}`).digest('hex').slice(0, 32).toUpperCase()}`,
      device_id: this.profile.device_id,
      phone,
      hardware: this.evidence.hardware,
    });
    if (response.valid) return this.persistActive(response, 'activation');
    this.current = safeState('activation_required', {
      reason: response.reason || 'key_not_activated',
      activation_key: response.canonical_key || null,
      phone: maskPhone(phone),
    });
    return this.current;
  }

  async updatePhone(value) {
    const phone = normalizePhone(value);
    if (!phone) {
      const error = new Error('invalid phone');
      error.code = 'invalid_phone';
      throw error;
    }
    const saved = this.store.loadLicense();
    if (!saved?.license_token) throw Object.assign(new Error('license token missing'), { code: 'license_token_missing' });
    const response = await this.api.updatePhone({ ...(await this.proof('update_phone')), license_token: saved.license_token, phone });
    return this.persistActive(response, 'phone_update');
  }

  retry() {
    if (this.current.state === 'activation_required' && this.profile?.phone) {
      return this.submitPhone(this.profile.phone);
    }
    return this.initialize();
  }
}

module.exports = { LicenseManager, maskKey, maskPhone, newProfile };
