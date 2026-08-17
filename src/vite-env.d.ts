/// <reference types="vite/client" />

interface MiaDeviceIdentity {
  algorithm: 'Ed25519';
  publicKeyPem: string;
  fingerprint: string;
}

interface MiaRuntimeBridge {
  platform: string;
  getDeviceIdentity(): Promise<MiaDeviceIdentity>;
  signDeviceChallenge(challenge: string): Promise<string>;
  storeLicenseToken(token: string): Promise<boolean>;
}

interface Window {
  miaRuntime?: MiaRuntimeBridge;
}
