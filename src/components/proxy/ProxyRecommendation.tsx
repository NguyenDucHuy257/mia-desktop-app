const recommendations = [
  ['1–3 MST', 'Thường chưa cần Proxy'],
  ['4–10 MST', 'Có thể dùng 1–2 Proxy'],
  ['10–30 MST', 'Có thể dùng 3–5 Proxy'],
  ['Nhiều hơn', 'Tăng dần theo nhu cầu thực tế'],
];

export function ProxyRecommendation() {
  return <section className="proxy-guide-section" id="proxy-recommendation">
    <span className="proxy-section-number">05</span>
    <div className="proxy-section-heading"><h2>Tôi cần mua bao nhiêu?</h2><p>Chọn mức vừa đủ, theo số MST bạn thường xử lý trong một đợt.</p></div>
    <div className="proxy-recommendation-table" role="table" aria-label="Gợi ý số lượng Proxy">
      <div role="row" className="proxy-recommendation-head"><span role="columnheader">Số MST thường tải</span><span role="columnheader">Gợi ý</span></div>
      {recommendations.map(([range, advice]) => <div role="row" key={range}><strong role="cell">{range}</strong><span role="cell">{advice}</span></div>)}
    </div>
    <p className="proxy-reference-note"><strong>Gợi ý tham khảo:</strong> đây không phải cam kết hiệu năng. Hãy bắt đầu với số lượng nhỏ và điều chỉnh sau khi sử dụng thực tế.</p>
  </section>;
}
