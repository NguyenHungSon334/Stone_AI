-- Enable pgvector
create extension if not exists vector;

create table if not exists products (
    id uuid primary key default gen_random_uuid(),

    -- Identity
    ma_sp text unique not null,
    ten_sp text not null,
    the_loai text,
    danh_muc text,

    -- Specs
    kich_thuoc text,
    khoi_luong float,
    trong_luong float,
    don_vi text,
    quy_cach text,

    -- Pricing per stone type
    gia_da_xanh_den bigint,
    gia_da_xanh_reu bigint,
    gia_da_xam_bd bigint,
    gia_da_grn_an_do bigint,

    -- Meta
    mo_ta text,
    ghi_chu text,
    tags text[],
    ban_chay boolean default false,
    ton_kho int default 0,

    -- Media
    anh_urls text[],
    video_url text,

    -- AI embedding
    embedding vector(1536),

    created_at timestamptz default now()
);

create index if not exists products_embedding_idx
    on products using ivfflat (embedding vector_cosine_ops)
    with (lists = 100);

create index if not exists products_the_loai_idx on products(the_loai);
create index if not exists products_danh_muc_idx on products(danh_muc);

-- pgvector search function
create or replace function match_products(
    query_embedding vector(1536),
    match_count int default 5,
    filter_the_loai text default null,
    filter_danh_muc text default null,
    filter_price_max bigint default null
)
returns table (
    id uuid,
    ma_sp text,
    ten_sp text,
    the_loai text,
    danh_muc text,
    kich_thuoc text,
    gia_da_xanh_den bigint,
    gia_da_xanh_reu bigint,
    gia_da_xam_bd bigint,
    gia_da_grn_an_do bigint,
    mo_ta text,
    ghi_chu text,
    tags text[],
    ton_kho int,
    similarity float
)
language sql stable
as $$
    select
        p.id, p.ma_sp, p.ten_sp, p.the_loai, p.danh_muc,
        p.kich_thuoc,
        p.gia_da_xanh_den, p.gia_da_xanh_reu,
        p.gia_da_xam_bd, p.gia_da_grn_an_do,
        p.mo_ta, p.ghi_chu, p.tags, p.ton_kho,
        1 - (p.embedding <=> query_embedding) as similarity
    from products p
    where
        (filter_the_loai is null or p.the_loai ilike '%' || filter_the_loai || '%')
        and (filter_danh_muc is null or p.danh_muc ilike '%' || filter_danh_muc || '%')
        and (
            filter_price_max is null
            or p.gia_da_xanh_den <= filter_price_max
            or p.gia_da_xanh_reu <= filter_price_max
            or p.gia_da_xam_bd <= filter_price_max
            or p.gia_da_grn_an_do <= filter_price_max
        )
    order by similarity desc
    limit match_count;
$$;
