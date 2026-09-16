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

const GUIDE_VIDEO_EMBED_PREFIX = 'https://www.youtube-nocookie.com/embed/';
const GUIDE_VIDEO_REFERRER = 'https://gotax.vn/';

function guideVideoRequestHeaders(url, requestHeaders = {}) {
  if (typeof url !== 'string' || !url.startsWith(GUIDE_VIDEO_EMBED_PREFIX)) return requestHeaders;
  return { ...requestHeaders, Referer: GUIDE_VIDEO_REFERRER };
}

module.exports = { GUIDE_VIDEO_EMBED_PREFIX, guideVideoRequestHeaders, isTrustedAppUrl };
