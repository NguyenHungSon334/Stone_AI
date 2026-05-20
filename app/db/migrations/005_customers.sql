-- Customer CRM table: one row per lead, upserted as AI collects info
create table if not exists customers (
    id uuid primary key default gen_random_uuid(),
    messenger_user_id text unique not null,

    -- Contact info
    name text,
    phone text,

    -- Project info (mirrors slot schema)
    project_type text,       -- mộ đơn | lăng tộc
    stone_type text,         -- xanh rêu | xanh đen | granite
    items jsonb,             -- ["mộ", "lăng thờ", "cổng", "lan can"]
    location text,           -- tỉnh/huyện thi công
    crane_access text,       -- có | không
    timeline text,           -- tháng/năm dự kiến

    -- Lead status
    status text default 'new',  -- new | contacted | qualified | closed
    notes text,

    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists customers_phone_idx on customers(phone);
create index if not exists customers_status_idx on customers(status);

-- Auto-update updated_at
create or replace function update_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

create or replace trigger customers_updated_at
    before update on customers
    for each row execute function update_updated_at();
