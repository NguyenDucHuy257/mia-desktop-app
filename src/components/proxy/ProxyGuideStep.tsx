import { useState } from 'react';
import { RECOMMENDED_PROXY, type ProxyGuideStepData } from './proxy-guide-config';

export function ProxyGuideStep({ step }: { step: ProxyGuideStepData }) {
  const [preview, setPreview] = useState(false);
  return <><article className="proxy-purchase-step">
    <div className="proxy-step-copy">
      <span className="proxy-step-label">Bước {step.number}</span>
      <h3>{step.title}</h3>
      <p>{step.description}</p>
      {step.recommended ? <div className="proxy-product-card">
        <small>Loại MIA khuyên dùng</small>
        <strong>{RECOMMENDED_PROXY.name}</strong>
        <span>{RECOMMENDED_PROXY.note}</span>
        <em>{RECOMMENDED_PROXY.duration}</em>
      </div> : null}
    </div>
    {step.image ? <button className="proxy-step-image" type="button" aria-label={`Xem lớn ảnh ${step.title}`} onClick={() => setPreview(true)}><img src={step.image} alt={`Hướng dẫn ${step.title}`} /></button>
      : <div className="proxy-step-placeholder" aria-label={`Chưa có ảnh cho ${step.title}`}><span>▧</span><small>Ảnh hướng dẫn sẽ hiển thị tại đây</small></div>}
  </article>{preview && step.image ? <div className="proxy-image-lightbox" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setPreview(false); }}><figure role="dialog" aria-modal="true" aria-label={`Ảnh ${step.title}`}><button type="button" onClick={() => setPreview(false)} aria-label="Đóng ảnh">×</button><img src={step.image} alt={`Hướng dẫn ${step.title}`} /></figure></div> : null}</>;
}
