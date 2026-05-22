-- saaz initial schema. Demo dataset of Persian songwriters/artists.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------------------------------------------------------------------------
-- Core: artist
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artist (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug            TEXT NOT NULL UNIQUE,                  -- url-safe identifier
    name_en         TEXT NOT NULL,
    name_fa         TEXT,                                  -- Persian script
    name_translit   TEXT,                                  -- common transliteration
    genre           TEXT NOT NULL CHECK (genre IN ('persian_jazz', 'indie_persian_jazz', 'traditional', 'other')),
    sub_genres      TEXT[] DEFAULT '{}',                   -- e.g. {fusion, electroacoustic}
    era             TEXT CHECK (era IN ('classical', '20th_century', 'contemporary', 'emerging')),
    status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'upcoming', 'legacy', 'deceased')),
    country         TEXT DEFAULT 'IR',                     -- ISO 3166; diaspora artists may be elsewhere
    based_in        TEXT,                                  -- city, country
    born            DATE,
    died            DATE,
    bio             TEXT,
    bio_source      TEXT,                                  -- 'seed', 'wikipedia', 'perplexity', 'anthropic_web'
    embedding       vector(1536),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_artist_genre ON artist (genre);
CREATE INDEX IF NOT EXISTS idx_artist_status ON artist (status);
CREATE INDEX IF NOT EXISTS idx_artist_embedding
    ON artist USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Links: YouTube, Instagram, Wikipedia, Spotify, Bandcamp, etc.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artist_link (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_id     UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    kind          TEXT NOT NULL CHECK (kind IN (
                      'youtube', 'youtube_channel', 'instagram', 'wikipedia',
                      'spotify', 'apple_music', 'bandcamp', 'soundcloud',
                      'official_site', 'twitter', 'other'
                  )),
    url           TEXT NOT NULL,
    label         TEXT,                                    -- e.g. song title for youtube links
    verified      BOOLEAN NOT NULL DEFAULT FALSE,
    last_checked  TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artist_id, url)
);

CREATE INDEX IF NOT EXISTS idx_link_artist ON artist_link (artist_id);
CREATE INDEX IF NOT EXISTS idx_link_kind ON artist_link (kind);

-- ---------------------------------------------------------------------------
-- Images (URLs only, optional mirror to Vercel Blob)
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS artist_image (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_id     UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    url           TEXT NOT NULL,                           -- original URL
    mirror_url    TEXT,                                    -- Vercel Blob mirror (optional)
    caption       TEXT,
    source        TEXT,                                    -- 'wikipedia', 'instagram', etc.
    license       TEXT,                                    -- 'CC BY-SA 4.0', 'fair_use', 'unknown'
    is_primary    BOOLEAN NOT NULL DEFAULT FALSE,
    width         INT,
    height        INT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (artist_id, url)
);

CREATE INDEX IF NOT EXISTS idx_image_artist ON artist_image (artist_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_image_one_primary
    ON artist_image (artist_id) WHERE is_primary = TRUE;

-- ---------------------------------------------------------------------------
-- Songs and notable works
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS song (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_id     UUID NOT NULL REFERENCES artist(id) ON DELETE CASCADE,
    title_en      TEXT NOT NULL,
    title_fa      TEXT,
    title_translit TEXT,
    album         TEXT,
    year          INT,
    youtube_url   TEXT,
    spotify_url   TEXT,
    is_notable    BOOLEAN NOT NULL DEFAULT FALSE,
    embedding     vector(1536),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_song_artist ON song (artist_id);
CREATE INDEX IF NOT EXISTS idx_song_year ON song (year);
CREATE INDEX IF NOT EXISTS idx_song_embedding
    ON song USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ---------------------------------------------------------------------------
-- Enrichment runs: every external API call we make
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS enrichment_run (
    id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    artist_id      UUID REFERENCES artist(id) ON DELETE SET NULL,
    ran_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    source         TEXT NOT NULL,         -- 'wikipedia', 'perplexity', 'anthropic_web', 'youtube'
    model          TEXT,                  -- e.g. 'claude-sonnet-4-5', 'sonar-medium-online'
    prompt_hash    TEXT,                  -- sha256 of the prompt for dedupe
    status         TEXT NOT NULL CHECK (status IN ('success', 'partial', 'error', 'rate_limited')),
    rows_written   INT DEFAULT 0,
    cost_usd       NUMERIC(10, 6),
    error_message  TEXT,
    raw_response   JSONB                  -- truncated for debugging
);

CREATE INDEX IF NOT EXISTS idx_enrichment_artist ON enrichment_run (artist_id);
CREATE INDEX IF NOT EXISTS idx_enrichment_time ON enrichment_run (ran_at DESC);

-- ---------------------------------------------------------------------------
-- Provenance: per-fact source tracking so we can audit any claim
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS data_provenance (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    fact_table    TEXT NOT NULL,         -- 'artist', 'artist_link', 'song', ...
    fact_id       UUID NOT NULL,
    fact_column   TEXT,                  -- 'bio', 'born', 'youtube_url'
    source        TEXT NOT NULL,         -- 'seed', 'wikipedia', 'perplexity', 'anthropic_web', 'user'
    source_url    TEXT,
    confidence    REAL NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    retrieved_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS idx_provenance_fact
    ON data_provenance (fact_table, fact_id);
