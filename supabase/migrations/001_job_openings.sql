-- Public LinkedIn guest job listings. Apply in the Supabase SQL editor.
-- Service-role requests bypass RLS. No policies are granted to anon or authenticated.

create table if not exists public.job_openings (
  linkedin_job_id text primary key,
  company_name text,
  job_url text not null unique,
  designation text,
  location text,
  description text,
  hiring_team text,
  linkedin_listed_at timestamptz,
  first_seen_at timestamptz not null default now(),
  search_keywords text not null,
  search_location text not null
);

alter table public.job_openings enable row level security;

insert into storage.buckets (id, name, public)
values ('job-exports', 'job-exports', false)
on conflict (id) do nothing;
