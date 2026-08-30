import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../../src/App';
import { AppShell } from '../../src/components/AppShell';

describe('MIA desktop shell', () => {
  it('renders the production shell without synthetic customer rows', () => {
    const html = renderToStaticMarkup(createElement(App));

    expect(html).toContain('MIA WT');
    expect(html).toContain('Quản lý HDDT');
    expect(html).toContain('Đồng bộ dữ liệu');
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

  it('does not expose a separate production PDF navigation item', () => {
    const html = renderToStaticMarkup(createElement(AppShell, {
      active: 'xml-html', onNavigate: () => undefined, children: createElement('main'),
    }));
    expect(html).not.toMatch(/<span>PDF<\/span>/);
  });
});
