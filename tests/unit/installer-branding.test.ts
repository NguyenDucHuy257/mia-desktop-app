import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';

const packageMetadata = JSON.parse(readFileSync('package.json', 'utf8'));

function bitmapDimensions(path: string) {
  const bitmap = readFileSync(path);
  return {
    width: bitmap.readInt32LE(18),
    height: Math.abs(bitmap.readInt32LE(22)),
  };
}

describe('MIA TOOL 2026 installer branding', () => {
  it('uses the new product and installer names without numeric suffixes', () => {
    expect(packageMetadata.version).toBe('4.0.6');
    expect(packageMetadata.build.productName).toBe('MIA TOOL 2026');
    expect(packageMetadata.build.nsis.shortcutName).toBe('MIA TOOL 2026');
    expect(packageMetadata.build.nsis.artifactName).toBe('MIA-TOOL-2026-Setup-${version}.${ext}');
  });

  it('configures an assisted installer with welcome, terms, logo artwork, and install directory selection', () => {
    const nsis = packageMetadata.build.nsis;
    expect(nsis.oneClick).toBe(false);
    expect(nsis.allowToChangeInstallationDirectory).toBe(true);
    expect(nsis.include).toBe('installer-resources/installer.nsh');
    expect(nsis.license).toBe('installer-resources/installer-license.txt');
    expect(nsis.installerSidebar).toBe('installer-resources/installerSidebar.bmp');
    expect(nsis.installerHeader).toBe('installer-resources/installerHeader.bmp');

    const welcome = readFileSync(nsis.include, 'utf8');
    const terms = readFileSync(nsis.license, 'utf8');
    expect(welcome).toContain('Chào mừng đến với MIA TOOL 2026');
    expect(welcome).toContain('Hướng dẫn cài đặt:');
    expect(welcome).toContain('Màn hình nền hoặc menu Bắt đầu');
    expect(welcome).toContain('MUI_PAGE_WELCOME');
    expect(terms).toContain('ĐIỀU KHOẢN SỬ DỤNG MIA TOOL 2026');
    expect(terms).toContain('kiểm tra, đối chiếu số liệu');
  });

  it('stores Vietnamese NSIS text as UTF-8 with BOM', () => {
    for (const path of ['installer-resources/installer.nsh', 'installer-resources/installer-license.txt']) {
      expect([...readFileSync(path).subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    }
  });

  it('provides NSIS artwork at the required bitmap dimensions', () => {
    expect(bitmapDimensions('installer-resources/installerSidebar.bmp')).toEqual({ width: 164, height: 314 });
    expect(bitmapDimensions('installer-resources/installerHeader.bmp')).toEqual({ width: 150, height: 57 });
  });

  it('keeps the established packaged user-data directory after the product rename', () => {
    const main = readFileSync('electron/main.cjs', 'utf8');
    expect(main).toContain("const LEGACY_USER_DATA_DIRECTORY = ['MIA', 'WT'].join(' ');");
    expect(main).toContain("app.setPath('userData', path.join(app.getPath('appData'), LEGACY_USER_DATA_DIRECTORY));");
  });

  it('uses the new visible brand throughout application entry points', () => {
    const sources = [
      'index.html',
      'src/App.tsx',
      'src/components/AppShell.tsx',
      'src/features/licensing/LicenseGate.tsx',
      'electron/main.cjs',
    ].map((path) => readFileSync(path, 'utf8'));
    for (const source of sources) {
      expect(source).not.toMatch(/MIA WT/);
    }
    expect(sources.join('\n')).toContain('MIA TOOL 2026');
  });
});
