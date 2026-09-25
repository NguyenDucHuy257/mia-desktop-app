import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../../src/App';
import { AppShell } from '../../src/components/AppShell';
import { LicenseUpdateContext } from '../../src/features/licensing/LicensePolicyContext';

describe('MIA desktop shell', () => {
  it('fails closed without the Electron license bridge', () => {
    const html = renderToStaticMarkup(createElement(App));

    expect(html).toContain('MIA TOOL 2026');
    expect(html).toContain('Không thể kiểm tra bản quyền');
    expect(html).not.toContain('Quản lý HĐĐT');
    expect(html).not.toContain('Đồng bộ dữ liệu');
    expect(html).not.toContain('0101234567');
    expect(html.match(/class="table-row table-grid"/g)).toBeNull();
  });

  it('uses one shared active navigation state and one XML/HTML/PDF item', () => {
    const html = renderToStaticMarkup(createElement(AppShell, {
      active: 'xml-html', onNavigate: () => undefined, children: createElement('main'),
    }));
    expect(html).toContain('XML/HTML/PDF');
    expect(html.match(/data-active="true"/g)).toHaveLength(1);
    expect(html).toMatch(/data-active="true"[^>]*>[\s\S]*?XML\/HTML\/PDF/);
  });

  it('shows the update notice supplied by the first server key-list line', () => {
    const shell = createElement(AppShell, {
      active: 'invoices', onNavigate: () => undefined, children: createElement('main'),
    });
    const html = renderToStaticMarkup(createElement(LicenseUpdateContext.Provider, {
      value: {
        available: true,
        url: 'https://drive.google.com/file/d/release/view',
        label: 'v4.0.8 (11/09/2026)',
        latest_version: '4.0.8',
        current_version: '4.0.7',
      },
      children: shell,
    }));
    expect(html).toContain('Đã có phiên bản MIA TOOL 2026 4.0.8');
    expect(html).toContain('Tải bản cập nhật');
    expect(html).toContain('data-available="true"');
  });

  it('does not expose a separate production PDF navigation item', () => {
    const html = renderToStaticMarkup(createElement(AppShell, {
      active: 'xml-html', onNavigate: () => undefined, children: createElement('main'),
    }));
    expect(html).not.toMatch(/<span>PDF<\/span>/);
  });

  it('renders the ordered navigation, support card and customer hotlines', () => {
    const html = renderToStaticMarkup(createElement(AppShell, {
      active: 'invoices', onNavigate: () => undefined, children: createElement('main'),
    }));
    const labels = ['Quản lý HĐĐT', 'XML/HTML/PDF', 'Xuất tờ khai thuế GTGT', 'Tra cứu PDF gốc', 'Tra cứu MVT', 'Lịch sử tải xuống', 'Cài đặt hệ thống', 'Hướng dẫn sử dụng'];
    expect(labels.map((label) => html.indexOf(label))).toEqual([...labels.map((label) => html.indexOf(label))].sort((a, b) => a - b));
    expect(html).not.toContain('Danh sách MST');
    expect(html).toContain('Hỗ trợ tận tâm');
    expect(html).toContain('0383.466.992 - 0865.219.286');
    expect(html).toContain('CÔNG TY CỔ PHẦN GIẢI PHÁP VÀ CÔNG NGHỆ SỐ WETECH');
    expect(html).toContain('Giải pháp tải HDDT hàng loạt');
    expect(html).toContain('Phiên bản MIA TOOL 2026 4.2.2');
  });
});
