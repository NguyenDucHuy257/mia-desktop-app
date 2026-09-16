import { expect, test as base } from '@playwright/test';

export const test = base.extend({
  page: async ({ page }, use) => {
    await page.addInitScript(() => {
      const activeState = () => ({ state: 'active', active: true, valid: true, expired: false, reason: 'ok' });
      const license = {
        status: async () => activeState(),
        initialize: async () => activeState(),
        submitPhone: async () => activeState(),
        retry: async () => activeState(),
        details: async () => ({ ...activeState(), phone: null, phone_status: 'verified', expires_at: null, device_bound: true, canonical_key: null, mode: null }),
        revealKey: async () => null,
        updatePhone: async () => activeState(),
      };
      const unlockedState = () => ({ state: 'unlocked', configured: true, unlocked: true, retry_after_seconds: 0 });
      const offlineAuth = {
        status: async () => unlockedState(),
        create: async () => unlockedState(),
        unlock: async () => unlockedState(),
        change: async () => unlockedState(),
      };
      const nativeDefineProperty = Object.defineProperty;
      const withSecurity = (value: unknown) => ({ ...(value && typeof value === 'object' ? value : {}), license, offlineAuth });
      nativeDefineProperty(window, 'miaRuntime', { configurable: true, writable: true, value: withSecurity(undefined) });
      Object.defineProperty = function defineProperty(target: object, property: PropertyKey, attributes: PropertyDescriptor) {
        if (target === window && property === 'miaRuntime' && 'value' in attributes) {
          return nativeDefineProperty(target, property, { ...attributes, value: withSecurity(attributes.value) });
        }
        return nativeDefineProperty(target, property, attributes);
      };
    });
    await use(page);
  },
});

export { expect };
