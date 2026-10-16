import Link from 'next/link';
import {
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  GitMerge,
  Layers3,
  LoaderCircle,
  ShieldCheck,
  SquareTerminal,
  TriangleAlert,
} from 'lucide-react';

const workflow = [
  { title: 'Mô tả', text: 'Kỹ sư dữ liệu đưa ra yêu cầu bằng ngôn ngữ tự nhiên.', owner: 'ENGINEER', gate: false },
  { title: 'Xác nhận kế hoạch', text: 'Hệ thống đề xuất Typed Spec. Con người kiểm tra và xác nhận.', owner: 'GATE', gate: true },
  { title: 'Duyệt mapping', text: 'Sinh mã dbt/Dagster tự động từ template chuẩn hóa.', owner: 'SYSTEM', gate: false },
  { title: 'Chạy sandbox', text: 'Kiểm thử trên dữ liệu thực tế. Thu thập bằng chứng ledger.', owner: 'SYSTEM', gate: false },
  { title: 'Phê duyệt & Vận hành', text: 'Reviewer đánh giá bằng chứng độc lập trước khi deploy.', owner: 'GATE', gate: true },
] as const;

const capabilities = [
  {
    label: 'KHÁM PHÁ',
    title: 'Data Catalog & Lineage',
    text: 'Theo dõi dữ liệu xuyên suốt từ Source tới Gold, đính kèm metadata và các chính sách quản trị một cách mạch lạc.',
    tone: 'slate',
    visual: 'lineage',
  },
  {
    label: 'THỰC THI',
    title: 'Deterministic Code Generation',
    text: 'Sinh mã dbt và Dagster từ spec tĩnh, loại bỏ sự mơ hồ của AI bằng cách áp dụng template chuẩn hóa của tổ chức.',
    tone: 'indigo',
    visual: 'code',
  },
  {
    label: 'BẰNG CHỨNG',
    title: 'Evidence & Reconciliation',
    text: 'Mọi pipeline đều chạy qua sandbox thực tế, thu thập số liệu đối soát để đảm bảo dữ liệu đầu ra không sai lệch.',
    tone: 'emerald',
    visual: 'evidence',
  },
  {
    label: 'VẬN HÀNH',
    title: 'Day-2 Monitoring & Drift',
    text: 'Liên tục giám sát dữ liệu và schema sau khi deploy, cảnh báo sớm khi xuất hiện sai lệch so với hợp đồng dữ liệu.',
    tone: 'slate',
    visual: 'drift',
  },
] as const;

const coreCapabilities = [
  ['Governed planning', <>LLM hỗ trợ lên ý tưởng, chuyển hóa mong muốn thành <code>[Typed Spec]</code> rõ ràng. Spec là hợp đồng ràng buộc mọi bước tiếp theo.</>],
  ['Real-data sandbox', <>Kiểm thử an toàn với dữ liệu cô lập <code>[Sandbox]</code>. Pipeline chạy thật trên mẫu dữ liệu để phát hiện lỗi runtime trước khi triển khai.</>],
  ['Evidence ledger', <>Mọi thay đổi và kết quả chạy thử được ghi vào <code>[Ledger]</code> bất biến, tạo vết audit rõ ràng cho compliance.</>],
  ['Independent reconciliation', <>Tự động so khớp đầu ra với <code>[Source Truth]</code>. Các bài kiểm tra chất lượng chạy độc lập để xác nhận độ tin cậy.</>],
  ['Human review', <>Áp dụng nguyên tắc <code>[Four-eyes Review]</code>, yêu cầu người thứ hai kiểm tra bằng chứng trước khi cấp quyền vận hành.</>],
  ['Scheduled operation', <>Tích hợp với công cụ orchestration qua <code>[Compiled Code]</code>, giúp pipeline vận hành ổn định trong dài hạn.</>],
] as const;

function ProductPreview() {
  return <div className="landing-product-preview">
    <div className="landing-preview-head">
      <span><SquareTerminal size={17} aria-hidden /><b className="landing-mono">REQUEST COMPOSER</b></span>
      <span className="landing-ready"><CheckCircle2 size={13} aria-hidden />READY_FOR_REVIEW</span>
    </div>
    <p className="landing-request">“Tạo bảng tổng hợp doanh thu theo tuần từ source ERP, join với danh mục sản phẩm, chỉ lấy các giao dịch đã thanh toán.”</p>
    <ol className="landing-preview-steps">
      <li><i>1</i><span>Generate Typed Spec</span><Check size={15} aria-hidden /></li>
      <li><i>2</i><span>Compile dbt/Dagster</span><Check size={15} aria-hidden /></li>
      <li><i>3</i><span>Run Sandbox &amp; Reconcile</span><LoaderCircle className="landing-spin" size={15} aria-hidden /></li>
    </ol>
  </div>;
}

function CapabilityVisual({ type }: { type: typeof capabilities[number]['visual'] }) {
  if (type === 'lineage') return <div className="landing-card-visual landing-lineage">
    <div><span>GOLD</span><strong>fct_weekly_revenue</strong></div>
    <p><GitMerge size={14} aria-hidden /> Upstream: stg_orders, stg_products</p>
  </div>;
  if (type === 'code') return <pre className="landing-code"><em>import</em> dlt{`\n`}<em>@dlt.resource</em>(name=<mark>&apos;orders&apos;</mark>){`\n`}<em>def</em> fetch_data():{`\n`}  ...</pre>;
  if (type === 'evidence') return <div className="landing-card-visual landing-evidence">
    <div><span>Row Count Check</span><CheckCircle2 size={16} aria-hidden /></div>
    <i><b /></i><small>100% MATCH</small>
  </div>;
  return <div className="landing-card-visual landing-drift">
    <span><TriangleAlert size={20} aria-hidden /></span>
    <div><strong>Schema Drift Detected</strong><small>Missing column &apos;tax_rate&apos; in upstream</small></div>
  </div>;
}

export default function LandingPage() {
  return <main id="top" className="landing">
    <header className="landing-nav">
      <a href="#top" className="landing-brand" aria-label="Atomesus — về đầu trang"><Layers3 size={27} aria-hidden /><span>Atomesus</span></a>
      <nav aria-label="Điều hướng landing page">
        <a href="#san-pham">SẢN PHẨM</a>
        <a href="#quy-trinh">QUY TRÌNH</a>
        <a href="#bang-chung">BẰNG CHỨNG</a>
        <a href="#van-hanh">VẬN HÀNH</a>
      </nav>
      <div className="landing-nav-actions">
        <span className="landing-online"><i />SYSTEM ONLINE</span>
        <Link href="/runs">Vào Pipeline Studio</Link>
      </div>
    </header>

    <section className="landing-hero" aria-labelledby="landing-title">
      <div className="landing-mesh" />
      <div className="landing-container landing-hero-grid">
        <div className="landing-hero-copy">
          <h1 id="landing-title">Biến yêu cầu dữ liệu thành <em>pipeline được kiểm chứng.</em></h1>
          <p>Mô tả kết quả đầu ra. Atomesus tự động sinh mã dlt/dbt từ template chuẩn, chạy thử trên sandbox, đối soát độc lập và cung cấp bằng chứng để bạn phê duyệt trước khi vận hành.</p>
          <div className="landing-actions">
            <Link className="landing-button primary" href="/runs/new">Yêu cầu pipeline <ArrowRight size={17} aria-hidden /></Link>
            <a className="landing-button secondary" href="#quy-trinh">Xem cách hoạt động</a>
          </div>
        </div>
        <ProductPreview />
      </div>
      <div className="landing-wave" aria-hidden><div /></div>
    </section>

    <section className="landing-proof" aria-label="Công nghệ và quy trình">
      <div className="landing-container">
        <span>SOURCE → BRONZE → STAGING → GOLD</span>
        <span>PLAN → SPEC → CODE → SANDBOX → REVIEW</span>
        <span>DBT</span><span>DAGSTER</span><span>POSTGRES</span>
      </div>
    </section>

    <section id="quy-trinh" className="landing-section landing-workflow">
      <div className="landing-container">
        <div className="landing-section-heading centered">
          <h2>Từ yêu cầu đến <em>bằng chứng</em></h2>
          <p>Quy trình kiểm soát con người ở các bước quyết định, đảm bảo mọi pipeline được thực thi đúng ý định kinh doanh.</p>
        </div>
        <ol className="landing-workflow-grid">
          {workflow.map((step, index) => <li key={step.title}>
            <b>{index + 1}</b><h3>{step.title}</h3><p>{step.text}</p><span className={step.gate ? 'gate' : ''}>{step.owner}</span>
          </li>)}
        </ol>
      </div>
    </section>

    <section id="san-pham" className="landing-section landing-products">
      <div className="landing-container">
        <div className="landing-section-heading"><h2>Nền tảng được thiết kế cho sự <em>chính xác</em> và minh bạch.</h2></div>
        <div className="landing-capability-grid">
          {capabilities.map((item, index) => <article key={item.title} className={`landing-capability ${item.tone} ${index % 2 ? 'stagger' : ''}`}>
            <div className="landing-capability-copy"><div><h3>{item.title}</h3><span>{item.label}</span></div><p>{item.text}</p></div>
            <CapabilityVisual type={item.visual} />
          </article>)}
        </div>
      </div>
    </section>

    <section id="nang-luc" className="landing-section landing-core">
      <div className="landing-container landing-core-grid">
        <div className="landing-core-intro">
          <span className="landing-eyebrow">NĂNG LỰC CỐT LÕI</span>
          <h2>Nền móng cho sự <em>chắc chắn</em></h2>
          <p>Atomesus được xây dựng quanh tính minh bạch. Từ lúc lên kế hoạch đến khi đưa vào hoạt động, con người luôn nắm quyền kiểm soát tại các chốt chặn quan trọng.</p>
          <Link className="landing-button primary" href="/runs">Khám phá tính năng</Link>
        </div>
        <div className="landing-accordion">
          {coreCapabilities.map(([title, content], index) => <details key={title} open={index === 0 ? true : undefined}>
            <summary>{title}<ChevronDown size={19} aria-hidden /></summary>
            <div><p>{content}</p></div>
          </details>)}
        </div>
      </div>
    </section>

    <section id="bang-chung" className="landing-trust">
      <div className="landing-trust-inner">
        <ShieldCheck size={52} aria-hidden />
        <h2>Atomesus chứng minh mã máy hoạt động đúng Spec.<br /><em>Bạn xác nhận Spec giải quyết đúng bài toán.</em></h2>
        <p>Chúng tôi vạch rõ ranh giới niềm tin: máy móc đảm bảo tính tất định và xác thực kỹ thuật. Trách nhiệm về ý nghĩa nghiệp vụ luôn thuộc về con người.</p>
      </div>
    </section>

    <footer id="van-hanh" className="landing-footer">
      <div className="landing-footer-glow" />
      <div className="landing-footer-shell">
        <div className="landing-final-cta">
          <h2>Sẵn sàng xây dựng <em>pipeline</em> có kiểm chứng?</h2>
          <div className="landing-actions">
            <Link className="landing-button light" href="/runs/new">Mở Pipeline Studio</Link>
            <Link className="landing-button dark-outline" href="/relationships">Xem kiến trúc</Link>
          </div>
        </div>
        <div className="landing-footer-grid">
          <div><div className="landing-footer-brand"><Layers3 size={22} aria-hidden /><strong>Atomesus</strong></div><p>Nền tảng kỹ thuật dữ liệu hỗ trợ bởi AI, tự động sinh và kiểm chứng pipeline dựa trên hợp đồng đặc tả.</p></div>
          <div><h3>PRODUCT</h3><ul><li><Link href="/runs">Pipeline Studio</Link></li><li><Link href="/domains">Data Catalog</Link></li><li><Link href="/runs/review">Independent Reconciliation</Link></li></ul></div>
          <div><h3>DOCUMENTATION</h3><ul><li><a href="#nang-luc">Typed Specification</a></li><li><a href="#quy-trinh">Sandbox Environment</a></li><li><a href="https://github.com/AI20K-Build-Phase-Cohort-4/P-045">GitHub Repository</a></li></ul></div>
        </div>
        <div className="landing-footer-bottom"><span>© ATOMESUS PROJECT</span><span>DESIGNED FOR DETERMINISTIC DATA ENGINEERING</span></div>
      </div>
    </footer>
  </main>;
}
