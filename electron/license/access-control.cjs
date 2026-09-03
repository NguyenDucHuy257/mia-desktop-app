'use strict';

const LICENSE_REQUIRED = 'LICENSE_REQUIRED';

function isLicenseAccessGranted(state) {
  return Boolean(
    state
    && state.state === 'active'
    && state.active === true
    && state.valid === true
    && state.expired === false
    && state.reason === 'ok',
  );
}

function licenseRequiredError() {
  const error = new Error(LICENSE_REQUIRED);
  error.name = 'LicenseRequiredError';
  error.code = LICENSE_REQUIRED;
  return error;
}

module.exports = { LICENSE_REQUIRED, isLicenseAccessGranted, licenseRequiredError };
