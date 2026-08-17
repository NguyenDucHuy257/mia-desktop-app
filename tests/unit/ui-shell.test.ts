import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { describe, expect, it } from 'vitest';
import App from '../../src/App';

describe('MIA desktop shell', () => {
  it('renders the Figma-derived navigation, controls, and seeded table rows', () => {
    const html = renderToStaticMarkup(createElement(App));

    expect(html).toContain('MIA WT');
    expect(html).toContain('Quản lý HDDT');
    expect(html).toContain('Đồng bộ dữ liệu');
    expect(html).toContain('0101234567');
    expect(html.match(/class="table-row table-grid"/g)).toHaveLength(10);
  });
});
