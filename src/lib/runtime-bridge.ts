import type { AccountConnection } from './api/contracts';

export interface MiaDeviceIdentity {
  algorithm: 'Ed25519';
  publicKeyPem: string;
  fingerprint: string;
}

export interface MiaAccountCredentials {
  username: string;
  password: string;
}

export interface MiaAccountConnectionsBridge {
  create(credentials: MiaAccountCredentials): Promise<AccountConnection>;
  get(connectionId: string): Promise<AccountConnection>;
  reconnect(connectionId: string, credentials: MiaAccountCredentials): Promise<AccountConnection>;
  revoke(connectionId: string): Promise<void>;
}

export interface MiaRuntimeBridge {
  platform: string;
  getDeviceIdentity(): Promise<MiaDeviceIdentity>;
  signDeviceChallenge(challenge: string): Promise<string>;
  storeLicenseToken(token: string): Promise<boolean>;
  accountConnections: MiaAccountConnectionsBridge;
}

declare global {
  interface Window {
    miaRuntime?: MiaRuntimeBridge;
  }
}
