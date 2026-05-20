create table if not exists messages (
    id uuid primary key default gen_random_uuid(),
    messenger_user_id text not null,

    role text not null,         -- user | assistant | tool
    content text,
    tool_name text,
    tool_input jsonb,

    -- AI metadata
    model_used text,
    tokens_input int,
    tokens_output int,
    cost_usd float,
    latency_ms int,

    created_at timestamptz default now()
);

create index if not exists msg_user_created_idx
    on messages(messenger_user_id, created_at desc);
