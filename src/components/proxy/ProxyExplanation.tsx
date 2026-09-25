import { ProxyFlowDiagram } from './ProxyFlowDiagram';

export function ProxyExplanation() {
  return <>
    <section className="proxy-guide-section" id="proxy-why">
      <span className="proxy-section-number">01</span>
      <div className="proxy-section-heading"><h2>Tại sao nhiều MST phải chờ nhau?</h2></div>
      <div className="proxy-readable-copy"><p>Khi MIA tải dữ liệu hóa đơn, mỗi tài khoản cần kết nối đến hệ thống hóa đơn điện tử.</p><p>Trong cơ chế hiện tại, các tài khoản sử dụng chung một kết nối Internet của máy tính.</p><p>Để tránh nhiều tài khoản cùng sử dụng một kết nối tại cùng thời điểm, MIA sẽ lần lượt xử lý từng tài khoản.</p></div>
      <div className="proxy-basic-diagram"><span>Internet hiện tại</span><i>↓</i><span>MIA</span><i>↓</i><div><b>MST 1 <small>đang tải</small></b><b>MST 2 <small>chờ</small></b><b>MST 3 <small>chờ</small></b><b>MST 4 <small>chờ</small></b></div></div>
      <p className="proxy-highlight-copy">Vì vậy, khi có nhiều MST, tổng thời gian tải sẽ tăng lên.</p>
    </section>
    <section className="proxy-guide-section" id="proxy-how">
      <span className="proxy-section-number">02</span>
      <div className="proxy-section-heading"><h2>Muốn tải nhiều MST cùng lúc thì làm thế nào?</h2></div>
      <div className="proxy-readable-copy"><p>Cách đơn giản là bổ sung thêm các đường kết nối Internet riêng cho MIA.</p><p>Trong phần mềm, các kết nối bổ sung này được gọi là Proxy.</p></div>
      <div className="proxy-definition-card"><strong>Proxy</strong><span>=</span><b>một đường kết nối Internet bổ sung</b></div>
      <p className="proxy-muted-copy">Bạn không cần cài thêm mạng, đổi Wi‑Fi hay cấu hình modem. MIA chỉ cần thông tin Proxy để sử dụng thêm kết nối đó khi tải dữ liệu.</p>
    </section>
    <section className="proxy-guide-section" id="proxy-flow">
      <span className="proxy-section-number">03</span>
      <div className="proxy-section-heading"><h2>Mỗi Proxy = thêm 1 luồng tải</h2><p>So sánh trước và sau khi bổ sung đường mạng cho MIA.</p></div>
      <ProxyFlowDiagram />
      <p className="proxy-highlight-copy">Như vậy, mua thêm 1 Proxy sẽ giúp MIA có thêm 1 luồng tải song song.</p>
      <div className="proxy-capacity-grid"><span><strong>Không có Proxy</strong><b>1</b><small>tài khoản cùng lúc</small></span><span><strong>1 Proxy</strong><b>2</b><small>luồng tải</small></span><span><strong>3 Proxy</strong><b>4</b><small>luồng tải</small></span><span><strong>5 Proxy</strong><b>6</b><small>luồng tải</small></span></div>
      <p className="proxy-caution">Tốc độ thực tế còn phụ thuộc vào số lượng hóa đơn, tốc độ mạng và phản hồi của hệ thống hóa đơn điện tử.</p>
    </section>
    <section className="proxy-guide-section" id="proxy-example">
      <span className="proxy-section-number">04</span>
      <div className="proxy-section-heading"><h2>Ví dụ dễ hiểu</h2><p>Doanh nghiệp của bạn có 20 MST cần tải.</p></div>
      <div className="proxy-example-grid"><article><strong>Không sử dụng Proxy</strong><p>MIA xử lý lần lượt</p><div>MST 1 <i>↓</i> MST 2 <i>↓</i> MST 3 <i>↓</i> … <i>↓</i> MST 20</div></article><article><strong>Bổ sung 4 Proxy</strong><p>MIA chia thành 5 luồng</p><div>{[1, 2, 3, 4, 5].map((value) => <span key={value}>Luồng {value} <i>→</i> MST {String.fromCharCode(64 + value)}</span>)}</div></article></div>
      <p className="proxy-muted-copy">Khi một MST hoàn thành, luồng đó tiếp tục nhận MST tiếp theo.</p>
      <p className="proxy-highlight-copy">Càng có nhiều luồng phù hợp, danh sách nhiều MST càng được xử lý nhanh hơn.</p>
    </section>
  </>;
}
