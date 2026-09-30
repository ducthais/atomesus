export type Status = 'READY_FOR_REVIEW' | 'DEPLOYED' | 'BLOCKED_SEMANTIC_CONFLICT' | 'BLOCKED_BUDGET' | 'BLOCKED_AST' | 'VALIDATING' | 'APPROVED' | 'REJECTED' | 'NEEDS_HUMAN' | 'INFRA_ERROR' | 'INTERNAL_ERROR' | 'DEPLOY_FAILED';
export type Evidence = { rowSource: number; rowBronze: number; secondRunRows: number; testsPassed: number; testsTotal: number; bytes: number; usd: number; estimated: boolean; baselineSec: number; optimizedSec: number; hashMatch: boolean; metricMatch: boolean };
export type Run = { id: string; name: string; description: string; domain: string; source: string; status: Status; version: number; owner: string; date: string; hash: string; phase: number; feedback?: string; approvalHash?: string; model?:string; metric?:string; cron?:string; evidence?:Evidence; attempts?:{spec:number;sql:number;llm:number} };
export const labels: Record<Status, string> = { READY_FOR_REVIEW:'Chờ duyệt', DEPLOYED:'Đã triển khai', BLOCKED_SEMANTIC_CONFLICT:'Chặn ngữ nghĩa', BLOCKED_BUDGET:'Vượt ngân sách', BLOCKED_AST:'Chặn SQL', VALIDATING:'Đang thẩm định', APPROVED:'Đã duyệt', REJECTED:'Đã từ chối', NEEDS_HUMAN:'Cần xử lý', INFRA_ERROR:'Lỗi hạ tầng', INTERNAL_ERROR:'Lỗi hệ thống', DEPLOY_FAILED:'Triển khai lỗi' };
export const phaseIds = ['intake','plan','confirm','resolve','propose','arbitrate','render','static','budget','el_run','el_validate','t_run','t_validate','optimize','report','review','deploy','persist'] as const;
export const phases = ['Tiếp nhận','Lập kế hoạch','Xác nhận','Phân giải','Đề xuất','Phân xử','Render','Kiểm tra tĩnh','Ngân sách','Chạy dlt','Đối soát E-L','Chạy dbt','Thẩm định T','Tối ưu','Báo cáo','Review','Deploy Dagster','Lưu mẫu'];
export const phaseGroups = [{name:'Tiếp nhận',start:0,end:2},{name:'Phân giải',start:3,end:3},{name:'Sinh',start:4,end:6},{name:'Kiểm',start:7,end:8},{name:'Chạy',start:9,end:13},{name:'Quyết định',start:14,end:17}];
export const demoEvidence: Evidence = {rowSource:12480,rowBronze:12480,secondRunRows:12480,testsPassed:6,testsTotal:6,bytes:1_200_000_000,usd:0.0075,estimated:true,baselineSec:4.2,optimizedSec:0.9,hashMatch:true,metricMatch:true};
export function hasDemoFixture(run: Run) {
 const request = run.description.trim().replace(/\s+/g,' ').replace(/[.!]$/,'').toLowerCase();
 return run.domain === 'Sales' && run.source === 'orders' && [
  'đưa đơn hàng từ postgres về warehouse, cập nhật mỗi giờ và tính doanh thu theo ngày',
  'đưa bảng đơn hàng từ postgres về warehouse, cập nhật mỗi giờ, rồi tính doanh thu theo ngày',
 ].includes(request);
}
export const initialRuns: Run[] = [
 {id:'RUN-024',name:'Doanh thu theo ngày',description:'Đưa đơn hàng từ Postgres về warehouse, cập nhật mỗi giờ và tính doanh thu theo ngày.',domain:'Sales',source:'orders',status:'READY_FOR_REVIEW',version:3,owner:'minh',date:'Vừa cập nhật',hash:'a7f3c902b842',phase:15,evidence:demoEvidence,attempts:{spec:1,sql:2,llm:5}},
 {id:'RUN-023',name:'Hợp nhất hồ sơ khách hàng',description:'Hợp nhất customer giữa Sales và Marketing.',domain:'Sales',source:'customers',status:'BLOCKED_SEMANTIC_CONFLICT',version:1,owner:'minh',date:'12 phút trước',hash:'b830fc129102',phase:3},
 {id:'RUN-022',name:'Đồng bộ danh mục sản phẩm',description:'Đồng bộ bảng products vào bronze theo giờ.',domain:'Sales',source:'products',status:'NEEDS_HUMAN',version:2,owner:'an',date:'38 phút trước',hash:'c098fac201aa',phase:10,feedback:'Nguồn products chưa có fixture để thẩm định trong prototype.'},
 {id:'RUN-021',name:'Hiệu quả chiến dịch',description:'Tổng hợp hiệu quả của toàn bộ chiến dịch marketing.',domain:'Marketing',source:'campaigns',status:'BLOCKED_BUDGET',version:1,owner:'minh',date:'1 giờ trước',hash:'d17709fa0091',phase:8},
 {id:'RUN-020',name:'Đơn hàng theo kênh bán',description:'Tổng hợp đơn hàng theo kênh bán hàng.',domain:'Sales',source:'orders',status:'DEPLOYED',version:2,owner:'an',date:'2 giờ trước',hash:'ea201fa00122',phase:17,evidence:demoEvidence,attempts:{spec:1,sql:2,llm:5}},
];
export const policies: Record<string,string> = {
 'domains.yml': 'domains:\n  sales:\n    datasets: [sales_raw]\n    steward: an@company.vn\n    entities:\n      customer:\n        canonical: false\n        definition: "Tài khoản đã ký hợp đồng"\n        tables: [sales_raw.customers, sales_raw.accounts]\n  marketing:\n    datasets: [mkt_raw]\n    steward: binh@company.vn\n    entities:\n      customer:\n        canonical: false\n        definition: "Người để lại email"\n        tables: [mkt_raw.leads, mkt_raw.contacts]\n\nconformed_dimensions: []',
 'budgets.yml': 'pricing:\n  usd_per_tb: 6.25\nper_run:\n  max_bytes_scanned: 5_000_000_000\n  max_usd: 0.05\n  max_llm_calls: 6\nper_day:\n  max_usd: 2.00\nrules:\n  - require_partition_filter_on: [sales_raw.orders]\n  - forbid_select_star_in: [gold]\n  - max_tables_joined: 6',
 'objectives.yml': 'sales:\n  hard:\n    freshness_minutes: 60\n  soft:\n    cost_usd_per_day: 1.0\n  prefer: cheapest_feasible\n  degrade_order: [freshness, completeness]\nmarketing:\n  hard:\n    freshness_minutes: 1440\n  soft:\n    cost_usd_per_day: 0.5\n  prefer: cheapest_feasible'
};
export function filesFor(run: Run) {
 const model = run.model || 'fct_daily_revenue';
 if (run.source !== 'orders') {
  const note = '# Ch?a c? fixture cho ngu?n n?y. Backend s? render code t? spec v? schema th?t.\n';
  return [
   {path:`ingestion/${run.source}_pipeline.py`,language:'python',before:'',after:note},
   {path:`models/${model}.sql`,language:'sql',before:'',after:'-- Ch? spec t? backend.\n'},
   {path:`models/${model}.yml`,language:'yaml',before:'',after:'# Ch? spec t? backend.\n'},
   {path:`dagster/${model}_asset.py`,language:'python',before:'',after:note},
  ];
 }
 return [
  {path:'ingestion/orders_pipeline.py',language:'python',before:`import dlt\n\n@dlt.resource(write_disposition="replace")\ndef orders(rows):\n    yield from rows\n`,after:`import dlt\n\n@dlt.resource(\n    primary_key="order_id",\n    write_disposition="merge",\n)\ndef orders(rows):\n    # Cursor updated_at; ngu?n ch? ??c.\n    yield from rows\n\n# Preview: backend c?u h?nh Postgres v? DuckDB.\n`},
  {path:`models/${model}.sql`,language:'sql',before:`{{ config(materialized='table') }}\n\nselect order_date, channel, sum(amount) as revenue\nfrom {{ source('bronze', 'raw_orders') }}\nwhere status <> 'refunded'\ngroup by 1, 2\n`,after:`{{ config(\n    materialized='incremental',\n    unique_key=['order_date', 'channel'],\n    partition_by='order_date'\n) }}\n\nwith normalized as (\n    select order_id, cast(order_date as date) as order_date,\n           channel, status,\n           case when currency = 'USD'\n                then amount * 25400 else amount end as amount_vnd\n    from {{ source('bronze', 'raw_orders') }}\n), daily as (\n    select order_date, channel,\n           sum(amount_vnd) as revenue,\n           count(distinct order_id) as order_count\n    from normalized\n    where status <> 'refunded'\n    {% if is_incremental() %}\n      and order_date >= (select max(order_date) from {{ this }})\n    {% endif %}\n    group by 1, 2\n)\nselect order_date, channel, revenue, order_count from daily\n`},
  {path:`models/${model}.yml`,language:'yaml',before:`version: 2\nmodels:\n  - name: ${model}\n`,after:`version: 2\nmodels:\n  - name: ${model}\n    columns:\n      - name: order_date\n        tests: [not_null]\n      - name: channel\n        tests: [not_null]\n      - name: revenue\n        tests: [not_null]\n`},
  {path:`dagster/${model}_asset.py`,language:'python',before:'# Ch?a c? asset.\n',after:`# Preview orchestration; backend render Definitions v? resources.\nfrom dagster import Definitions, ScheduleDefinition, define_asset_job\nfrom dagster_dlt import DagsterDltResource, dlt_assets\nfrom dagster_dbt import DbtCliResource, dbt_assets\n\n# raw_orders (dlt) ? stg_orders (dbt) ? ${model} (dbt)\n`}
 ];
}
