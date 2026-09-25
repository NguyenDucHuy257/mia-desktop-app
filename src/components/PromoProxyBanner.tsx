import { useState } from 'react';
import { ProxySpeedIntroModal } from './proxy/ProxySpeedIntroModal';
import proxyRocket from '../assets/proxy-guide/proxy-rocket.png';
import '../styles/promo-proxy-banner.css';

export function PromoProxyBanner() {
  const [open, setOpen] = useState(false);

  return <>
    <button
      className="promo-proxy-banner"
      type="button"
      onClick={() => setOpen(true)}
      aria-label="Tìm hiểu cách tăng tốc tải nhiều mã số thuế với PROXY"
    >
      <svg className="promo-proxy-paper" viewBox="0 0 1200 56" preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="promo-proxy-red" x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#8f0712"><animate attributeName="stop-color" values="#8f0712;#c20c22;#7d0713;#8f0712" dur="5.8s" repeatCount="indefinite" /></stop>
            <stop offset="0.48" stopColor="#d7192d"><animate attributeName="stop-color" values="#d7192d;#ff4b2b;#e20d3d;#d7192d" dur="4.6s" repeatCount="indefinite" /></stop>
            <stop offset="1" stopColor="#a40718"><animate attributeName="stop-color" values="#a40718;#7e0c25;#d81724;#a40718" dur="6.4s" repeatCount="indefinite" /></stop>
            <animateTransform attributeName="gradientTransform" type="rotate" values="0 .5 .5;18 .5 .5;0 .5 .5" dur="5s" repeatCount="indefinite" />
          </linearGradient>
          <filter id="promo-proxy-shadow" x="-5%" y="-25%" width="110%" height="150%">
            <feDropShadow dx="0" dy="3" stdDeviation="2.5" floodColor="#59030b" floodOpacity=".28" />
          </filter>
        </defs>
        <path filter="url(#promo-proxy-shadow)" fill="url(#promo-proxy-red)" d="M8 7 22 5 33 8 47 3 62 6 78 4 92 8 108 3 126 6 143 4 161 7 178 3 196 6 214 4 232 8 250 3 269 6 289 4 307 7 327 3 347 6 367 4 387 8 407 3 427 6 447 4 467 7 487 3 507 6 527 4 547 8 567 3 587 6 607 4 627 7 647 3 667 6 687 4 707 8 727 3 747 6 767 4 787 7 807 3 827 6 847 4 867 8 887 3 907 6 927 4 947 7 967 3 987 6 1007 4 1027 8 1047 3 1067 6 1087 4 1107 7 1127 3 1147 6 1164 4 1177 8 1192 6 1189 17 1195 28 1190 39 1193 49 1178 47 1165 52 1148 49 1130 53 1112 49 1094 52 1075 48 1056 53 1037 49 1018 52 999 48 980 53 961 49 942 52 923 48 904 53 885 49 866 52 847 48 828 53 809 49 790 52 771 48 752 53 733 49 714 52 695 48 676 53 657 49 638 52 619 48 600 53 581 49 562 52 543 48 524 53 505 49 486 52 467 48 448 53 429 49 410 52 391 48 372 53 353 49 334 52 315 48 296 53 277 49 258 52 239 48 220 53 201 49 182 52 163 48 144 53 125 49 106 52 88 48 72 53 56 49 42 52 29 48 12 50 9 39 4 29 10 18Z" />
      </svg>
      <span className="promo-proxy-content">
        <span className="promo-proxy-title"><img src={proxyRocket} alt="" aria-hidden="true" />Tải nhiều MST cùng lúc nhanh hơn</span>
        <span className="promo-proxy-cta">Tìm hiểu ngay <b aria-hidden="true">→</b></span>
      </span>
    </button>
    {open ? <ProxySpeedIntroModal onClose={() => setOpen(false)} /> : null}
  </>;
}
