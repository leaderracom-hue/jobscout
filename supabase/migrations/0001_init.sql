-- Job Scout v1 schema (phase 1, 25 Nov 2026). Multi-tenant: every user row is protected by row-level security.
-- Jobs are global and deduped (one posting, scored per user); everything personal hangs off auth.users.
-- Not applied anywhere yet: needs a Supabase project chosen by the owner.

create extension if not exists pgcrypto;

-- ---------- per user ----------
create table public.profiles (
  user_id        uuid primary key references auth.users on delete cascade,
  full_name      text,
  headline       text,
  city           text,
  country        text,
  citizenship    text,
  authorized_eu  boolean default false,
  needs_sponsorship boolean default false,
  languages      text[] default '{}',
  contact        jsonb  default '{}'::jsonb,   -- email, linkedin, phone, portfolio (shown on generated CVs only)
  brief_md       text,                           -- the candidate brief the scorer reads (generated from the CV, edited by the user)
  search_prefs   jsonb  default '{}'::jsonb,   -- regions, employment, arrangement, sources, countries, eu_open
  rules          jsonb  default '{}'::jsonb,   -- exclude_words, keep_words, blocked_companies, followed_companies
  locale         text   default 'he',
  plan           text   default 'free',
  created_at     timestamptz default now()
);

create table public.cvs (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users on delete cascade,
  lang        text not null check (lang in ('he', 'en')),
  file_name   text not null,
  storage_path text not null,                    -- private bucket "cvs/<user_id>/..."
  brain       jsonb,                             -- family, target roles, ATS keywords, choose_when, weak_for
  created_at  timestamptz default now(),
  unique (user_id, lang, file_name)
);

-- ---------- shared postings ----------
create table public.jobs (
  id          uuid primary key default gen_random_uuid(),
  job_key     text not null unique,              -- company|title key (core.rules.key_of) or canonical URL hash
  title       text not null,
  company     text,
  location    text,
  url         text,
  links       jsonb default '{}'::jsonb,
  boards      text[] default '{}',
  easy_apply  boolean default false,
  posted_at   timestamptz,
  description text,                              -- kept short-lived; LinkedIn postings keep title/company/link only
  first_seen  timestamptz default now()
);

-- ---------- a job as seen by one user ----------
create table public.user_jobs (
  user_id     uuid not null references auth.users on delete cascade,
  job_id      uuid not null references public.jobs on delete cascade,
  keywords    text[] default '{}',
  fit         int,
  pro         int,
  elig        int,
  elig_status text,
  verdict     text,
  why         text,
  gaps        text[] default '{}',
  cv_file     text,
  evals       jsonb,
  status      text default 'open' check (status in ('open', 'sent', 'removed', 'auto', 'later')),
  status_reason text,
  status_at   timestamptz,
  found_at    timestamptz default now(),
  primary key (user_id, job_id)
);
create index user_jobs_open on public.user_jobs (user_id, status, fit desc);

create table public.searches (
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users on delete cascade,
  keyword     text not null,
  hours       int not null,
  prefs       jsonb,
  status      text default 'queued' check (status in ('queued', 'running', 'done', 'error')),
  found       int, new_jobs int, error text,
  created_at  timestamptz default now(),
  finished_at timestamptz
);

create table public.usage_events (
  id          bigserial primary key,
  user_id     uuid references auth.users on delete set null,
  search_id   uuid references public.searches on delete set null,
  provider    text not null,                     -- anthropic | apify | smartprospect ...
  tokens_in   int default 0,
  tokens_out  int default 0,
  usd         numeric(10, 5) default 0,
  at          timestamptz default now()
);
create index usage_by_user_month on public.usage_events (user_id, at);

create table public.generated_docs (               -- tailored CVs, cover letters, notes (history)
  id          uuid primary key default gen_random_uuid(),
  user_id     uuid not null references auth.users on delete cascade,
  job_id      uuid references public.jobs on delete set null,
  kind        text not null check (kind in ('cv', 'cover', 'note')),
  base_cv     text,
  body        text not null,
  created_at  timestamptz default now()
);

-- ---------- row-level security: a user sees only their own rows ----------
alter table public.profiles       enable row level security;
alter table public.cvs            enable row level security;
alter table public.user_jobs      enable row level security;
alter table public.searches       enable row level security;
alter table public.usage_events   enable row level security;
alter table public.generated_docs enable row level security;
alter table public.jobs           enable row level security;

create policy own_profile  on public.profiles       for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_cvs      on public.cvs            for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_jobs     on public.user_jobs      for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_searches on public.searches       for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_docs     on public.generated_docs for all using (user_id = auth.uid()) with check (user_id = auth.uid());
create policy own_usage    on public.usage_events   for select using (user_id = auth.uid());   -- written by the worker only
-- postings: readable when the user has the job in their list; written by the worker (service role) only
create policy jobs_seen    on public.jobs for select using (exists (select 1 from public.user_jobs u where u.job_id = jobs.id and u.user_id = auth.uid()));

-- CV files: private bucket, folder per user
insert into storage.buckets (id, name, public) values ('cvs', 'cvs', false) on conflict do nothing;
create policy cv_files_own on storage.objects for all
  using (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text)
  with check (bucket_id = 'cvs' and (storage.foldername(name))[1] = auth.uid()::text);
