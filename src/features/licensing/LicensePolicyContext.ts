import { createContext, useContext } from 'react';
import type { LicenseEntitlements, LicenseUpdateInfo } from '../../lib/runtime-bridge';

export const LicensePolicyContext = createContext<LicenseEntitlements | null>(null);
export const useLicensePolicy = () => useContext(LicensePolicyContext);
export const LicenseUpdateContext = createContext<LicenseUpdateInfo | null>(null);
export const useLicenseUpdate = () => useContext(LicenseUpdateContext);
