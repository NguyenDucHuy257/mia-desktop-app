function isTrustedAppUrl(candidateUrl, { devServerUrl, productionEntryUrl }) {
  try {
    const candidate = new URL(candidateUrl);

    if (devServerUrl) {
      const devServer = new URL(devServerUrl);
      return candidate.origin === devServer.origin;
    }

    const productionEntry = new URL(productionEntryUrl);
    return candidate.protocol === 'file:' && candidate.pathname === productionEntry.pathname;
  } catch {
    return false;
  }
}

module.exports = { isTrustedAppUrl };
