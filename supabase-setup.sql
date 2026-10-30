-- =====================================================================================
-- WaveRise community: ringtones & wallpapers, likes, follows, reports.
-- Run once in Supabase: Dashboard -> SQL Editor -> New query -> paste all of this -> Run.
-- Safe to run again after an update (it only adds what is missing).
-- =====================================================================================

-- ---------- admins: people who can see reports and remove anything ----------
create table if not exists public.admins (email text primary key);
alter table public.admins enable row level security;               -- nobody can read or change it from the site
-- Put the email you sign in with here (Google or email link). Add more lines for more admins.
insert into public.admins (email) values ('instavault8@gmail.com') on conflict do nothing;

create or replace function public.is_admin() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.admins where lower(email) = lower(coalesce(auth.jwt() ->> 'email', '')))
$$;

-- ---------- profiles: one per account, created automatically at first sign-in ----------
create table if not exists public.profiles (
  id uuid primary key references auth.users (id) on delete cascade,
  username text not null unique check (username ~ '^[a-z0-9_.]{3,20}$'),
  display_name text not null default '' check (char_length(display_name) <= 40),
  avatar_url text check (avatar_url is null or (char_length(avatar_url) <= 500 and avatar_url ~ '^https://')),
  bio text not null default '' check (char_length(bio) <= 160),
  followers_count int not null default 0,
  following_count int not null default 0,
  uploads_count int not null default 0,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;

-- usernames come from the person's name, never from their email (emails are never shown)
create or replace function public.make_username(seed text) returns text
language plpgsql security definer set search_path = public as $$
declare base text; u text; n int := 0;
begin
  base := regexp_replace(lower(coalesce(seed, '')), '[^a-z0-9]+', '_', 'g');
  base := trim(both '_' from base);
  if char_length(base) < 3 then base := 'user'; end if;
  base := left(base, 14);
  u := base;
  while exists (select 1 from public.profiles where username = u) or u in ('admin', 'waverise', 'explore', 'upload') loop
    n := n + 1;
    u := base || (floor(random() * 9000) + 1000)::int::text;
    if n > 30 then u := 'user' || (floor(random() * 90000000) + 10000000)::int::text; end if;
  end loop;
  return u;
end $$;

create or replace function public.handle_new_user() returns trigger
language plpgsql security definer set search_path = public as $$
declare nm text;
begin
  nm := coalesce(nullif(new.raw_user_meta_data ->> 'full_name', ''), nullif(new.raw_user_meta_data ->> 'name', ''), '');
  insert into public.profiles (id, username, display_name, avatar_url)
  values (new.id, public.make_username(nm), left(nm, 40),
          case when coalesce(new.raw_user_meta_data ->> 'avatar_url', new.raw_user_meta_data ->> 'picture', '') ~ '^https://'
               then left(coalesce(new.raw_user_meta_data ->> 'avatar_url', new.raw_user_meta_data ->> 'picture'), 500) end)
  on conflict (id) do nothing;
  return new;
end $$;
drop trigger if exists on_auth_user_created on auth.users;
create trigger on_auth_user_created after insert on auth.users for each row execute function public.handle_new_user();
-- accounts made before this script ran get a profile too
insert into public.profiles (id, username, display_name)
select u.id, public.make_username(coalesce(u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name')), left(coalesce(u.raw_user_meta_data ->> 'full_name', u.raw_user_meta_data ->> 'name', ''), 40)
from auth.users u where not exists (select 1 from public.profiles p where p.id = u.id);

-- people can edit their name, username, bio and picture, but not their counters
create or replace function public.protect_profile() returns trigger
language plpgsql as $$
begin
  new.id := old.id; new.created_at := old.created_at;
  if current_setting('wr.internal', true) is distinct from 'on' then          -- only WaveRise's own counters may change these
    new.followers_count := old.followers_count; new.following_count := old.following_count; new.uploads_count := old.uploads_count;
  end if;
  new.username := lower(new.username);
  return new;
end $$;
drop trigger if exists protect_profile on public.profiles;
create trigger protect_profile before update on public.profiles for each row execute function public.protect_profile();

-- ---------- items: ringtones and wallpapers ----------
create table if not exists public.items (
  id bigint generated always as identity primary key,
  user_id uuid not null references public.profiles (id) on delete cascade,
  kind text not null check (kind in ('ringtone', 'wallpaper')),
  title text not null check (char_length(btrim(title)) between 2 and 60),
  category text not null default 'other' check (category in
    ('bgm', 'love', 'devotional', 'sad', 'mass', 'funny', 'instrumental', 'notification',
     'nature', 'god', 'quotes', 'abstract', 'amoled', 'festival', 'cars', 'other')),
  tags text[] not null default '{}' check (coalesce(array_length(tags, 1), 0) <= 5),
  file_path text not null check (char_length(file_path) <= 200),
  thumb_path text check (thumb_path is null or char_length(thumb_path) <= 200),
  mime text not null check (mime in ('audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/ogg', 'audio/x-m4a', 'image/jpeg', 'image/webp', 'image/png')),
  size_bytes int not null check (size_bytes between 1 and 10485760),
  duration real check (duration is null or duration between 0 and 61),
  width int, height int,
  likes_count int not null default 0,
  downloads_count int not null default 0,
  reports_count int not null default 0,
  status text not null default 'live' check (status in ('live', 'hidden')),
  created_at timestamptz not null default now()
);
create index if not exists items_new on public.items (kind, status, created_at desc);
create index if not exists items_popular on public.items (kind, status, likes_count desc, created_at desc);
create index if not exists items_by_user on public.items (user_id, created_at desc);
alter table public.items enable row level security;

-- new uploads always start live with zero counts; 20 uploads a day per person
create or replace function public.new_item() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if (select count(*) from public.items where user_id = new.user_id and created_at > now() - interval '1 day') >= 20 then
    raise exception 'You can upload 20 files a day. Please try again tomorrow.' using errcode = 'P0001';
  end if;
  new.likes_count := 0; new.downloads_count := 0; new.reports_count := 0; new.status := 'live'; new.created_at := now();
  new.title := btrim(new.title);
  new.tags := array(select x from (select lower(left(btrim(t), 24)) x, min(n) n from unnest(new.tags) with ordinality u(t, n)
                                    where btrim(t) <> '' group by 1) q order by n limit 5);   -- tidy, no repeats, in the order typed
  return new;
end $$;
drop trigger if exists new_item on public.items;
create trigger new_item before insert on public.items for each row execute function public.new_item();

-- owners may change the title, category and tags; only admins may change the status or counters
create or replace function public.protect_item() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  new.id := old.id; new.user_id := old.user_id; new.kind := old.kind; new.file_path := old.file_path; new.thumb_path := old.thumb_path;
  new.mime := old.mime; new.size_bytes := old.size_bytes; new.duration := old.duration; new.width := old.width; new.height := old.height;
  new.created_at := old.created_at;
  if current_setting('wr.internal', true) is distinct from 'on' then
    new.likes_count := old.likes_count; new.downloads_count := old.downloads_count;
    if not public.is_admin() then new.status := old.status; new.reports_count := old.reports_count; end if;
  end if;
  return new;
end $$;
drop trigger if exists protect_item on public.items;
create trigger protect_item before update on public.items for each row execute function public.protect_item();

create or replace function public.count_uploads() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('wr.internal', 'on', true);
  if tg_op = 'INSERT' then update public.profiles set uploads_count = uploads_count + 1 where id = new.user_id;
  else update public.profiles set uploads_count = greatest(uploads_count - 1, 0) where id = old.user_id; end if;
  perform set_config('wr.internal', 'off', true);
  return null;
end $$;
drop trigger if exists count_uploads on public.items;
create trigger count_uploads after insert or delete on public.items for each row execute function public.count_uploads();

-- ---------- likes ----------
create table if not exists public.likes (
  user_id uuid not null references public.profiles (id) on delete cascade,
  item_id bigint not null references public.items (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (user_id, item_id)
);
create index if not exists likes_item on public.likes (item_id);
alter table public.likes enable row level security;

create or replace function public.count_likes() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('wr.internal', 'on', true);
  if tg_op = 'INSERT' then update public.items set likes_count = likes_count + 1 where id = new.item_id;
  else update public.items set likes_count = greatest(likes_count - 1, 0) where id = old.item_id; end if;
  perform set_config('wr.internal', 'off', true);
  return null;
end $$;
drop trigger if exists count_likes on public.likes;
create trigger count_likes after insert or delete on public.likes for each row execute function public.count_likes();

-- ---------- follows ----------
create table if not exists public.follows (
  follower_id uuid not null references public.profiles (id) on delete cascade,
  following_id uuid not null references public.profiles (id) on delete cascade,
  created_at timestamptz not null default now(),
  primary key (follower_id, following_id),
  check (follower_id <> following_id)
);
create index if not exists follows_following on public.follows (following_id);
alter table public.follows enable row level security;

create or replace function public.count_follows() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('wr.internal', 'on', true);
  if tg_op = 'INSERT' then
    update public.profiles set following_count = following_count + 1 where id = new.follower_id;
    update public.profiles set followers_count = followers_count + 1 where id = new.following_id;
  else
    update public.profiles set following_count = greatest(following_count - 1, 0) where id = old.follower_id;
    update public.profiles set followers_count = greatest(followers_count - 1, 0) where id = old.following_id;
  end if;
  perform set_config('wr.internal', 'off', true);
  return null;
end $$;
drop trigger if exists count_follows on public.follows;
create trigger count_follows after insert or delete on public.follows for each row execute function public.count_follows();

-- ---------- reports: 3 reports hide an upload until an admin looks at it ----------
create table if not exists public.reports (
  id bigint generated always as identity primary key,
  item_id bigint not null references public.items (id) on delete cascade,
  user_id uuid not null references public.profiles (id) on delete cascade,
  reason text not null check (reason in ('copyright', 'nudity', 'hate', 'spam', 'other')),
  created_at timestamptz not null default now(),
  unique (item_id, user_id)
);
alter table public.reports enable row level security;

create or replace function public.count_reports() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('wr.internal', 'on', true);
  update public.items set reports_count = reports_count + 1,
         status = case when reports_count + 1 >= 3 then 'hidden' else status end
   where id = new.item_id;
  perform set_config('wr.internal', 'off', true);
  return null;
end $$;
drop trigger if exists count_reports on public.reports;
create trigger count_reports after insert on public.reports for each row execute function public.count_reports();

-- ---------- downloads counter and account deletion ----------
create or replace function public.count_download(p_item bigint) returns void
language plpgsql security definer set search_path = public as $$
begin
  perform set_config('wr.internal', 'on', true);
  update public.items set downloads_count = downloads_count + 1 where id = p_item and status = 'live';
  perform set_config('wr.internal', 'off', true);
end $$;

create or replace function public.delete_my_account() returns void
language plpgsql security definer set search_path = public as $$
begin
  if auth.uid() is null then raise exception 'Not signed in'; end if;
  delete from auth.users where id = auth.uid();       -- removes the profile, uploads, likes, follows and reports with it
end $$;

-- ---------- who can do what (row level security) ----------
drop policy if exists "profiles are public" on public.profiles;
create policy "profiles are public" on public.profiles for select using (true);
drop policy if exists "edit own profile" on public.profiles;
create policy "edit own profile" on public.profiles for update to authenticated using (id = auth.uid()) with check (id = auth.uid());

drop policy if exists "see live items" on public.items;
create policy "see live items" on public.items for select using (status = 'live' or user_id = auth.uid() or public.is_admin());
drop policy if exists "upload own items" on public.items;
create policy "upload own items" on public.items for insert to authenticated
  with check (user_id = auth.uid() and file_path like auth.uid()::text || '/%' and (thumb_path is null or thumb_path like auth.uid()::text || '/%'));
drop policy if exists "edit own items" on public.items;
create policy "edit own items" on public.items for update to authenticated using (user_id = auth.uid() or public.is_admin());
drop policy if exists "delete own items" on public.items;
create policy "delete own items" on public.items for delete to authenticated using (user_id = auth.uid() or public.is_admin());

drop policy if exists "likes are public" on public.likes;
create policy "likes are public" on public.likes for select using (true);
drop policy if exists "like as yourself" on public.likes;
create policy "like as yourself" on public.likes for insert to authenticated
  with check (user_id = auth.uid() and exists (select 1 from public.items i where i.id = item_id and i.status = 'live'));
drop policy if exists "unlike own" on public.likes;
create policy "unlike own" on public.likes for delete to authenticated using (user_id = auth.uid());

drop policy if exists "follows are public" on public.follows;
create policy "follows are public" on public.follows for select using (true);
drop policy if exists "follow as yourself" on public.follows;
create policy "follow as yourself" on public.follows for insert to authenticated with check (follower_id = auth.uid());
drop policy if exists "unfollow own" on public.follows;
create policy "unfollow own" on public.follows for delete to authenticated using (follower_id = auth.uid());

drop policy if exists "report as yourself" on public.reports;
create policy "report as yourself" on public.reports for insert to authenticated
  with check (user_id = auth.uid() and exists (select 1 from public.items i where i.id = item_id and i.user_id <> auth.uid()));
drop policy if exists "admins read reports" on public.reports;
create policy "admins read reports" on public.reports for select to authenticated using (public.is_admin());
drop policy if exists "admins clear reports" on public.reports;
create policy "admins clear reports" on public.reports for delete to authenticated using (public.is_admin());

grant usage on schema public to anon, authenticated;
grant select on public.profiles, public.items, public.likes, public.follows to anon, authenticated;
grant update (username, display_name, bio, avatar_url) on public.profiles to authenticated;
grant insert, delete on public.items, public.likes, public.follows to authenticated;
grant update (title, category, tags, status, reports_count) on public.items to authenticated;
grant insert, select, delete on public.reports to authenticated;
revoke all on public.admins from anon, authenticated;
grant execute on function public.count_download(bigint) to anon, authenticated;
grant execute on function public.is_admin() to anon, authenticated;
grant execute on function public.delete_my_account() to authenticated;
revoke execute on function public.make_username(text) from anon, authenticated, public;

-- ---------- file storage: one public folder per person ----------
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('community', 'community', true, 10485760,
        array['audio/mpeg', 'audio/mp4', 'audio/aac', 'audio/ogg', 'audio/x-m4a', 'image/jpeg', 'image/webp', 'image/png'])
on conflict (id) do update set public = excluded.public, file_size_limit = excluded.file_size_limit, allowed_mime_types = excluded.allowed_mime_types;

drop policy if exists "community: upload to own folder" on storage.objects;
create policy "community: upload to own folder" on storage.objects for insert to authenticated
  with check (bucket_id = 'community' and (storage.foldername(name))[1] = auth.uid()::text);
drop policy if exists "community: delete own files or admin" on storage.objects;
create policy "community: delete own files or admin" on storage.objects for delete to authenticated
  using (bucket_id = 'community' and ((storage.foldername(name))[1] = auth.uid()::text or public.is_admin()));
