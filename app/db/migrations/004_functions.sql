-- Daily cost sum per user (used by cost cap guard)
create or replace function get_daily_cost(p_user_id text)
returns float
language sql
stable
as $$
    select coalesce(sum(cost_usd), 0)
    from messages
    where messenger_user_id = p_user_id
      and created_at >= current_date;
$$;

-- Auto-update updated_at on conversations
create or replace function touch_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists conversations_touch on conversations;
create trigger conversations_touch
    before update on conversations
    for each row execute function touch_updated_at();
