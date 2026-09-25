const waitingAccounts = ['MST 2', 'MST 3', 'MST 4'];
const parallelRows = [
  ['Internet chính', 'MST 1'],
  ['Proxy 1', 'MST 2'],
  ['Proxy 2', 'MST 3'],
  ['Proxy 3', 'MST 4'],
];

export function ProxyFlowDiagram() {
  return <div className="proxy-flow-comparison">
    <article className="proxy-flow-card">
      <header><span>Không có Proxy</span><strong>1 luồng tải</strong></header>
      <div className="proxy-single-flow">
        <span>Internet hiện tại</span><i>↓</i><span>MIA</span><i>↓</i><b>MST 1 · đang tải</b>
      </div>
      <div className="proxy-waiting-list">{waitingAccounts.map((name) => <span key={name}>{name}<small>chờ</small></span>)}</div>
    </article>
    <article className="proxy-flow-card proxy-flow-card--fast">
      <header><span>Có 3 Proxy</span><strong>4 luồng tải</strong></header>
      <div className="proxy-parallel-list">{parallelRows.map(([source, account]) => <div key={source}><span>{source}</span><i>→</i><b>{account}</b></div>)}</div>
    </article>
  </div>;
}
