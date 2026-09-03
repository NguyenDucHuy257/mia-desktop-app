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
      const nativeDefineProperty = Object.defineProperty;
      const withLicense = (value: unknown) => ({ ...(value && typeof value === 'object' ? value : {}), license });
      nativeDefineProperty(window, 'miaRuntime', { configurable: true, writable: true, value: withLicense(undefined) });
      Object.defineProperty = function defineProperty(target: object, property: PropertyKey, attributes: PropertyDescriptor) {
        if (target === window && property === 'miaRuntime' && 'value' in attributes) {
          return nativeDefineProperty(target, property, { ...attributes, value: withLicense(attributes.value) });
        }
        return nativeDefineProperty(target, property, attributes);
      };
    });
    await use(page);
  },
});

export { expect };
