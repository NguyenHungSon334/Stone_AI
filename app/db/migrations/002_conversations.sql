create table if not exists conversations (
    id uuid primary key default gen_random_uuid(),
    messenger_user_id text unique not null,
    name text,

    -- State machine
    state text default 'greeting',
    filled_slots jsonb default '{}',
    intent text,
    personality jsonb default '{}',

    -- Escalation
    is_escalated boolean default false,
    assigned_agent text,

    created_at timestamptz default now(),
    updated_at timestamptz default now()
);

create index if not exists conv_user_idx on conversations(messenger_user_id);
create index if not exists conv_state_idx on conversations(state);
