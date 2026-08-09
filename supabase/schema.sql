-- =============================================================================
-- Quran Competition Server — Supabase PostgreSQL schema
--
-- Run this once in the Supabase SQL Editor (Dashboard > SQL Editor > New query),
-- or via the Supabase CLI / psql over SSL.
-- It is idempotent-friendly (CREATE TABLE IF NOT EXISTS).
-- =============================================================================

-- gen_random_uuid() requires pgcrypto (enabled by default on Supabase).
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- competitions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.competitions (
    id                      uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    code                    text NOT NULL UNIQUE,
    name                    text NOT NULL,
    description             text,
    status                  text NOT NULL DEFAULT 'draft'
                            CHECK (status IN ('draft', 'scheduled', 'waiting',
                                              'running', 'paused', 'finished',
                                              'cancelled')),
    scheduled_at            timestamptz,
    started_at              timestamptz,
    finished_at             timestamptz,
    paused_seconds          double precision NOT NULL DEFAULT 0,
    default_points          integer NOT NULL DEFAULT 10,
    default_negative_points integer NOT NULL DEFAULT -2,
    speed_bonus_enabled     boolean NOT NULL DEFAULT FALSE,
    created_at              timestamptz NOT NULL DEFAULT now(),
    updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_competitions_status ON public.competitions (status);
CREATE INDEX IF NOT EXISTS idx_competitions_code   ON public.competitions (code);

-- ---------------------------------------------------------------------------
-- participants
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.participants (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    competition_id   uuid NOT NULL REFERENCES public.competitions (id) ON DELETE CASCADE,
    display_name     text NOT NULL CHECK (char_length(display_name) BETWEEN 2 AND 50),
    first_name       text,
    last_name        text,
    participant_code text NOT NULL UNIQUE,
    access_token     text NOT NULL UNIQUE,           -- opaque session token, never logged
    connected        boolean NOT NULL DEFAULT FALSE,
    joined_at        timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz,
    status           text NOT NULL DEFAULT 'joined'
);

CREATE INDEX IF NOT EXISTS idx_participants_competition_id
    ON public.participants (competition_id);
CREATE INDEX IF NOT EXISTS idx_participants_status
    ON public.participants (status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_participants_per_competition_display_name
    ON public.participants (competition_id, lower(display_name));

-- ---------------------------------------------------------------------------
-- questions
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.questions (
    id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    competition_id     uuid NOT NULL REFERENCES public.competitions (id) ON DELETE CASCADE,
    position           integer NOT NULL CHECK (position >= 1),
    text               text NOT NULL,
    type               text NOT NULL DEFAULT 'mcq'
                       CHECK (type IN ('mcq', 'true_false', 'text', 'number', 'audio')),
    duration_seconds   integer NOT NULL DEFAULT 15 CHECK (duration_seconds BETWEEN 1 AND 600),
    points             integer CHECK (points >= 0),
    negative_points    integer CHECK (negative_points <= 0),
    explanation        text,
    correct_answer_text text,                        -- official answer for text/number
    audio_url          text,
    started_at         timestamptz,                  -- server-authoritative window
    ends_at            timestamptz,
    -- Quran-specific (future use, kept generic)
    surah_number       integer CHECK (surah_number BETWEEN 1 AND 114),
    ayah_number        integer CHECK (ayah_number >= 1),
    page_number        integer CHECK (page_number >= 1),
    juz_number         integer CHECK (juz_number BETWEEN 1 AND 30),
    hizb_number        integer CHECK (hizb_number BETWEEN 1 AND 60),
    created_at         timestamptz NOT NULL DEFAULT now(),
    UNIQUE (competition_id, position)
);

CREATE INDEX IF NOT EXISTS idx_questions_competition_id
    ON public.questions (competition_id);
CREATE INDEX IF NOT EXISTS idx_questions_position
    ON public.questions (competition_id, position);

-- ---------------------------------------------------------------------------
-- choices (QCM / true_false)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.choices (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id uuid NOT NULL REFERENCES public.questions (id) ON DELETE CASCADE,
    text        text NOT NULL,
    position    integer NOT NULL CHECK (position >= 1),
    is_correct  boolean NOT NULL DEFAULT FALSE,     -- never sent to participants
    UNIQUE (question_id, position)
);

CREATE INDEX IF NOT EXISTS idx_choices_question_id
    ON public.choices (question_id);

-- ---------------------------------------------------------------------------
-- answers — the unique constraint below is the hard guard against duplicates.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS public.answers (
    id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    competition_id   uuid NOT NULL REFERENCES public.competitions (id) ON DELETE CASCADE,
    question_id      uuid NOT NULL REFERENCES public.questions (id) ON DELETE CASCADE,
    participant_id   uuid NOT NULL REFERENCES public.participants (id) ON DELETE CASCADE,
    choice_id        uuid REFERENCES public.choices (id) ON DELETE SET NULL,
    answer_text      text,
    submitted_at     timestamptz NOT NULL DEFAULT now(),
    response_time_ms integer NOT NULL,
    is_correct       boolean NOT NULL,
    points           double precision NOT NULL DEFAULT 0,
    bonus_points     double precision NOT NULL DEFAULT 0,
    -- ONE answer per participant per question (server + DB enforcement)
    UNIQUE (participant_id, question_id)
);

CREATE INDEX IF NOT EXISTS idx_answers_competition_id
    ON public.answers (competition_id);
CREATE INDEX IF NOT EXISTS idx_answers_question_id
    ON public.answers (question_id);
CREATE INDEX IF NOT EXISTS idx_answers_participant_id
    ON public.answers (participant_id);

-- =============================================================================
-- Security notes
--
-- 1. The backend talks to PostgREST with the SERVICE ROLE key, so ROW LEVEL
--    SECURITY policies do not restrict the server.
-- 2. The anon/publishable key (SUPABASE_KEY) is loaded by the server but not
--    exposed to any client in this version.
-- 3. Recommended hardening once the browser talks to Supabase directly:
--      ALTER TABLE public.competitions ENABLE ROW LEVEL SECURITY;
--      ALTER TABLE public.participants  ENABLE ROW LEVEL SECURITY;
--      ALTER TABLE public.questions     ENABLE ROW LEVEL SECURITY;
--      ALTER TABLE public.choices       ENABLE ROW LEVEL SECURITY;
--      ALTER TABLE public.answers       ENABLE ROW LEVEL SECURITY;
--    (RLS without policies denies everything to the anon key — secure default.)
-- =============================================================================