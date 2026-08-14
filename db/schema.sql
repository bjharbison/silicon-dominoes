-- ============================================================================
-- Silicon Dominoes — PostgreSQL schema
-- Build-order step 2 (ARCHITECTURE.md §14). Companion: test_schema.sql
-- Target: PostgreSQL 14+
--
-- Design invariants implemented here, not by convention (ARCHITECTURE.md §5):
--   IMMUTABILITY  published/scored data is append-only; UPDATE, DELETE and
--                 TRUNCATE are rejected by triggers on those tables.
--                 Corrections are new rows referencing what they correct.
--                 Ledger resolution lives in its own append-only table so a
--                 prediction row itself is never modified.
--   TRACEABILITY  events cannot commit without sources meeting the
--                 corroboration rule (deferred constraint trigger); cascade
--                 edges cannot exist without a ledger entry (FK).
--   TEMPORALITY   country_scores is keyed by (iso3, score_class, computed_at,
--                 methodology_version); "current" is a view, history is a query.
--
-- What stays OUTSIDE the database on purpose:
--   - JSON Schema validation of the five contracts (validate.py, pipeline step 6)
--   - source *independence* judgment and S4-pair detection (review UI)
--   - Goodhart caps, tier decay, hedging-prior checks (scoring code)
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- 0. Enumerated types (mirror schemas/common.schema.json)
-- ---------------------------------------------------------------------------
CREATE TYPE stack_layer AS ENUM
  ('power','facilities','silicon','networks','cloud','models','applications');

CREATE TYPE pole AS ENUM ('us','prc','sovereign','other');

CREATE TYPE event_direction AS ENUM ('us','prc','sovereign','reversal');

CREATE TYPE source_tier AS ENUM ('S1','S2','S3','S4','S5');

CREATE TYPE posture AS ENUM
  ('committed_us','committed_prc','leaning_us','leaning_prc',
   'hedged_active','sovereign_seeking','excluded','inert');

CREATE TYPE score_class AS ENUM ('T','D','E','P','H','derived');

CREATE TYPE prediction_class AS ENUM ('cascade','index');

CREATE TYPE forecast_source AS ENUM ('model','analyst');

CREATE TYPE controller_type AS ENUM
  ('state_pole','creditor','hub','regulatory_regime');

CREATE TYPE sub_state_actor_type AS ENUM
  ('state_telco','ministry','regulator','sovereign_fund','province',
   'state_enterprise','other');

CREATE TYPE mechanism_channel AS ENUM
  ('interconnector','cable_route','standards_harmonization','bundled_financing',
   'vendor_interoperability','ministerial_emulation','reference_deployment',
   'shared_creditor','sub_state_channel');

CREATE TYPE exercise_type AS ENUM
  ('license_denial','cutoff','forced_divestment','data_access_order',
   'update_withheld','spares_embargo','other');

CREATE TYPE cliff_type AS ENUM
  ('ran_contract','cloud_tender','dc_phase','financing_rollover',
   'license_renewal','other');

CREATE TYPE review_status AS ENUM
  ('pending','approved','rejected','needs_more_sourcing');

CREATE TYPE feed_class AS ENUM
  ('rss','structured_news','structured_data','watchlist');

CREATE TYPE gap_status AS ENUM ('open','resolved','wontfix');

-- ---------------------------------------------------------------------------
-- 1. Immutability machinery
-- ---------------------------------------------------------------------------
CREATE FUNCTION forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION
    'append-only violation: % on % rejected. Published data is immutable '
    '(ARCHITECTURE.md §5); corrections are new rows referencing the corrected id.',
    TG_OP, TG_TABLE_NAME
    USING ERRCODE = 'raise_exception';
END $$;

CREATE FUNCTION forbid_truncate() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION
    'append-only violation: TRUNCATE on % rejected (ARCHITECTURE.md §5).',
    TG_TABLE_NAME
    USING ERRCODE = 'raise_exception';
END $$;

-- Convenience: apply both triggers to a table.
CREATE PROCEDURE make_append_only(tbl regclass)
LANGUAGE plpgsql AS $$
BEGIN
  EXECUTE format(
    'CREATE TRIGGER trg_%s_immutable BEFORE UPDATE OR DELETE ON %s
       FOR EACH ROW EXECUTE FUNCTION forbid_change()',
    replace(tbl::text, '.', '_'), tbl);
  EXECUTE format(
    'CREATE TRIGGER trg_%s_no_truncate BEFORE TRUNCATE ON %s
       FOR EACH STATEMENT EXECUTE FUNCTION forbid_truncate()',
    replace(tbl::text, '.', '_'), tbl);
END $$;

-- ---------------------------------------------------------------------------
-- 2. Collection layer (ARCHITECTURE.md §3–4)
-- ---------------------------------------------------------------------------

-- Mutable operational config: which feeds exist and their health baselines.
CREATE TABLE feeds (
  feed_id        text PRIMARY KEY,
  feed_class     feed_class NOT NULL,
  url            text,
  facet_refs     jsonb,                 -- which versioned facet files build its queries
  active         boolean NOT NULL DEFAULT true,
  baseline_items_per_day numeric,
  last_capture_at        timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

-- Ground truth: every captured payload, verbatim, forever (append-only).
CREATE TABLE raw_captures (
  capture_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  feed_id        text NOT NULL REFERENCES feeds(feed_id),
  retrieved_at   timestamptz NOT NULL,
  url            text NOT NULL,
  sha256         char(64) NOT NULL,
  object_key     text NOT NULL,         -- content-addressed key in object storage
  snapshot_id    text,                  -- ArchiveBox / Wayback snapshot
  snapshot_url   text,
  parse_status   text NOT NULL DEFAULT 'unparsed',
  created_at     timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_raw_captures_feed_time ON raw_captures (feed_id, retrieved_at);
CREATE UNIQUE INDEX idx_raw_captures_dedupe ON raw_captures (feed_id, sha256);
CALL make_append_only('raw_captures');

-- ---------------------------------------------------------------------------
-- 3. Sources and events (system prompt §2, §3.3)
-- ---------------------------------------------------------------------------

-- One row per citation instance. Immutable: a citation is a record of what
-- was retrieved when; if a source changes, that is a new capture and row.
CREATE TABLE sources (
  source_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  url            text NOT NULL,
  archived_url   text NOT NULL,         -- link-rot guarantee: no snapshot, no citation
  snapshot_id    text,
  retrieved_at   timestamptz NOT NULL,
  source_tier    source_tier NOT NULL,
  publisher      text,
  title          text,
  language       text,
  capture_id     bigint REFERENCES raw_captures(capture_id),
  created_at     timestamptz NOT NULL DEFAULT now()
);
CALL make_append_only('sources');

-- Approved, scored events. Append-only. Rows arrive here ONLY through the
-- review workflow (see review_queue): approved_by is mandatory.
CREATE TABLE events (
  event_id           text PRIMARY KEY,
  event_date         date NOT NULL,               -- true event date
  retrieved_at       timestamptz NOT NULL,
  country_iso3       char(3) NOT NULL CHECK (country_iso3 ~ '^[A-Z]{3}$'),
  layer              stack_layer NOT NULL,
  instrument_tier    smallint NOT NULL CHECK (instrument_tier BETWEEN 1 AND 5),
  direction          event_direction NOT NULL,
  reversal_target    pole,
  reversed_tier      smallint CHECK (reversed_tier BETWEEN 1 AND 5),
  reversed_event_id  text REFERENCES events(event_id),
  depth_dimensions   text[] NOT NULL CHECK (
                       cardinality(depth_dimensions) >= 1
                       AND depth_dimensions <@ ARRAY['D','E','P','T']),
  p_subindicators    text[] CHECK (
                       p_subindicators IS NULL OR p_subindicators <@ ARRAY[
                       'p_license','p_update','p_spares','p_jurisdiction','p_telemetry']),
  summary            text NOT NULL CHECK (char_length(summary) >= 20),
  confidence         numeric(3,2) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
  analyst_inference  boolean NOT NULL DEFAULT false,
  inference_rationale text,
  correction         boolean NOT NULL DEFAULT false,
  corrects_event_id  text REFERENCES events(event_id),
  bundle_id          text,
  bundle_anchor      stack_layer,
  contested_decision boolean,
  controller_id      text,
  controller_name    text,
  sub_state_actor_name text,
  sub_state_actor_type sub_state_actor_type,
  announced_value_usd numeric CHECK (announced_value_usd IS NULL OR announced_value_usd >= 0),
  verified_value_usd  numeric CHECK (verified_value_usd  IS NULL OR verified_value_usd  >= 0),
  methodology_version text NOT NULL,
  review_id          bigint,                       -- backlink to review_queue
  approved_by        text NOT NULL,
  approved_at        timestamptz NOT NULL DEFAULT now(),
  created_at         timestamptz NOT NULL DEFAULT now(),
  search             tsvector GENERATED ALWAYS AS
                       (to_tsvector('english', coalesce(summary,''))) STORED,
  -- schema-mirrored conditionals (events.schema.json)
  CONSTRAINT chk_reversal_fields CHECK (
    direction <> 'reversal'
    OR (reversal_target IS NOT NULL AND reversed_tier IS NOT NULL)),
  CONSTRAINT chk_inference_rationale CHECK (
    NOT analyst_inference OR inference_rationale IS NOT NULL),
  CONSTRAINT chk_correction_ref CHECK (
    NOT correction OR corrects_event_id IS NOT NULL)
);
CREATE INDEX idx_events_country_date ON events (country_iso3, event_date);
CREATE INDEX idx_events_bundle ON events (bundle_id) WHERE bundle_id IS NOT NULL;
CREATE INDEX idx_events_search ON events USING gin (search);
CALL make_append_only('events');

CREATE TABLE event_sources (
  event_id   text   NOT NULL REFERENCES events(event_id),
  source_id  bigint NOT NULL REFERENCES sources(source_id),
  PRIMARY KEY (event_id, source_id)
);
CALL make_append_only('event_sources');

-- Corroboration rule (§2.2), enforced at COMMIT so events and their source
-- links can be inserted in either order within one transaction:
--   every event needs >= 1 non-S5 source;
--   Tier >= 3 needs >= 2 sources at S3-or-better, incl. >= 1 at S1/S2.
-- (The independence judgment and the "two S4s cannot corroborate" pairing
--  rule stay in the review UI; this is the mechanical floor.)
CREATE FUNCTION check_event_corroboration() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  n_non_s5 int;
  n_s3plus int;
  n_s12    int;
BEGIN
  SELECT
    count(*) FILTER (WHERE s.source_tier <> 'S5'),
    count(*) FILTER (WHERE s.source_tier IN ('S1','S2','S3')),
    count(*) FILTER (WHERE s.source_tier IN ('S1','S2'))
  INTO n_non_s5, n_s3plus, n_s12
  FROM event_sources es JOIN sources s USING (source_id)
  WHERE es.event_id = NEW.event_id;

  IF n_non_s5 < 1 THEN
    RAISE EXCEPTION
      'corroboration rule: event % has no S1–S4 source; S5 is never scored (§2.2)',
      NEW.event_id USING ERRCODE = 'raise_exception';
  END IF;
  IF NEW.instrument_tier >= 3 AND (n_s3plus < 2 OR n_s12 < 1) THEN
    RAISE EXCEPTION
      'corroboration rule: Tier >= 3 event % requires two independent S3+ sources '
      'including one S1/S2; found % S3+ of which % S1/S2 (§2.2)',
      NEW.event_id, n_s3plus, n_s12 USING ERRCODE = 'raise_exception';
  END IF;
  RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER trg_event_corroboration
  AFTER INSERT ON events
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_event_corroboration();

-- ---------------------------------------------------------------------------
-- 4. Review queue (mutable — candidates are not evidence yet)
-- ---------------------------------------------------------------------------
CREATE TABLE review_queue (
  review_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  capture_id       bigint REFERENCES raw_captures(capture_id),
  candidate        jsonb NOT NULL,        -- LLM-extracted candidate event
  status           review_status NOT NULL DEFAULT 'pending',
  reviewer         text,
  reviewed_at      timestamptz,
  rejection_reason text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX idx_review_pending ON review_queue (status) WHERE status = 'pending';

-- ---------------------------------------------------------------------------
-- 5. Ledger (doc 04) — predictions never edited; resolution is its own row
-- ---------------------------------------------------------------------------
CREATE TABLE test_sets (
  test_set_id   text PRIMARY KEY,
  enumerated_at timestamptz NOT NULL,
  frozen        boolean NOT NULL CHECK (frozen)   -- frozen at enumeration, always
);
CALL make_append_only('test_sets');

CREATE TABLE renewal_cliffs (
  cliff_id      text PRIMARY KEY,
  test_set_id   text NOT NULL REFERENCES test_sets(test_set_id),
  country_iso3  char(3) NOT NULL CHECK (country_iso3 ~ '^[A-Z]{3}$'),
  layer         stack_layer NOT NULL,
  cliff_type    cliff_type NOT NULL,
  expected_decision_date date NOT NULL,
  incumbent_pole pole,
  bundle_id     text,
  evidence_refs text[]
);
CALL make_append_only('renewal_cliffs');

CREATE TABLE ledger (
  prediction_id        text PRIMARY KEY,
  class                prediction_class NOT NULL,
  created_at           timestamptz NOT NULL DEFAULT now(),
  methodology_version  text NOT NULL,
  resolution_criterion text NOT NULL CHECK (char_length(resolution_criterion) >= 25),
  resolution_date      date NOT NULL,
  supersedes           text REFERENCES ledger(prediction_id),
  -- Class A (cascade)
  country_iso3         char(3) CHECK (country_iso3 IS NULL OR country_iso3 ~ '^[A-Z]{3}$'),
  claim                text CHECK (claim IS NULL OR char_length(claim) >= 25),
  mechanism            text CHECK (mechanism IS NULL OR (
                          char_length(mechanism) >= 25
                          AND mechanism !~* 'neighbou?rs?\s+(correlate|are\s+correlated)')),
  probability          numeric(4,3) CHECK (probability IS NULL OR probability BETWEEN 0 AND 1),
  -- Class B (index / renewal-cliff)
  cliff_id             text REFERENCES renewal_cliffs(cliff_id),
  test_set_id          text REFERENCES test_sets(test_set_id),
  forecast_source      forecast_source,
  derivation_version   text,
  distribution         jsonb,   -- {incumbent_retained, switch_us, ...}; sum checked in pipeline
  CONSTRAINT chk_class_fields CHECK (
    (class = 'cascade' AND claim IS NOT NULL AND mechanism IS NOT NULL
       AND probability IS NOT NULL AND country_iso3 IS NOT NULL)
    OR
    (class = 'index' AND cliff_id IS NOT NULL AND test_set_id IS NOT NULL
       AND forecast_source IS NOT NULL AND distribution IS NOT NULL)),
  CONSTRAINT chk_model_derivation CHECK (
    forecast_source IS DISTINCT FROM 'model' OR derivation_version IS NOT NULL)
);
CREATE INDEX idx_ledger_open ON ledger (resolution_date);
CALL make_append_only('ledger');

CREATE TABLE ledger_resolutions (
  prediction_id      text PRIMARY KEY REFERENCES ledger(prediction_id),
  resolved_at        timestamptz NOT NULL DEFAULT now(),
  outcome            text NOT NULL,   -- boolean-as-text for cascade; outcome enum value for index
  brier_contribution numeric NOT NULL CHECK (brier_contribution >= 0),
  evidence_refs      text[] NOT NULL CHECK (cardinality(evidence_refs) >= 1)
);
CALL make_append_only('ledger_resolutions');

CREATE TABLE exercise_observations (      -- doc 04 Class C
  observation_id  text PRIMARY KEY,
  observed_at     date NOT NULL,
  exercise_type   exercise_type NOT NULL,
  controller_id   text,
  controller_name text,
  controller_type controller_type,
  target_iso3     char(3) NOT NULL CHECK (target_iso3 ~ '^[A-Z]{3}$'),
  layer           stack_layer NOT NULL,
  p_subindicator  text CHECK (p_subindicator IN
                    ('p_license','p_update','p_spares','p_jurisdiction','p_telemetry')),
  event_refs      text[] NOT NULL CHECK (cardinality(event_refs) >= 1),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CALL make_append_only('exercise_observations');

-- ---------------------------------------------------------------------------
-- 6. Edges (doc 01 §3.1, §6) — derived per cycle, append-only
-- ---------------------------------------------------------------------------
CREATE TABLE exposure_edges (
  edge_id        text NOT NULL,
  cycle_date     date NOT NULL,
  country_iso3   char(3) NOT NULL CHECK (country_iso3 ~ '^[A-Z]{3}$'),
  controller_id  text NOT NULL,
  controller_type controller_type NOT NULL,
  controller_name text NOT NULL,
  controller_pole pole,
  layer          stack_layer NOT NULL,
  d_share        numeric(4,3) CHECK (d_share BETWEEN 0 AND 1),
  e_score        numeric(5,2) CHECK (e_score BETWEEN 0 AND 100),
  p              jsonb,                    -- sub-indicators, evidenced-or-null shape
  t_flow         numeric,
  sub_state_actor_name text,
  sub_state_actor_type sub_state_actor_type,
  bundle_ids     text[],
  evidence_refs  text[] NOT NULL CHECK (cardinality(evidence_refs) >= 1),
  confidence     numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
  methodology_version text NOT NULL,
  PRIMARY KEY (edge_id, cycle_date)
);
CREATE INDEX idx_exposure_country ON exposure_edges (country_iso3, cycle_date);
CREATE INDEX idx_exposure_controller ON exposure_edges (controller_id, cycle_date);
CALL make_append_only('exposure_edges');

CREATE TABLE cascade_edges (
  edge_id        text NOT NULL,
  cycle_date     date NOT NULL,
  from_iso3      char(3) NOT NULL CHECK (from_iso3 ~ '^[A-Z]{3}$'),
  to_iso3        char(3) NOT NULL CHECK (to_iso3 ~ '^[A-Z]{3}$'),
  layer          stack_layer,
  mechanism      text NOT NULL CHECK (
                   char_length(mechanism) >= 25
                   AND mechanism !~* 'neighbou?rs?\s+(correlate|are\s+correlated)'),
  mechanism_channel mechanism_channel NOT NULL,
  probability    numeric(4,3) NOT NULL CHECK (probability BETWEEN 0 AND 1),
  confidence     numeric(3,2) CHECK (confidence BETWEEN 0 AND 1),
  conf_band_low  numeric(3,2),
  conf_band_high numeric(3,2),
  projection     boolean NOT NULL DEFAULT true CHECK (projection),  -- structurally a projection
  ledger_ref     text NOT NULL REFERENCES ledger(prediction_id),    -- doc 04: no edge without entry
  network_basis  text[],
  methodology_version text NOT NULL,
  PRIMARY KEY (edge_id, cycle_date)
);
CALL make_append_only('cascade_edges');

-- ---------------------------------------------------------------------------
-- 7. Temporal country scores (ARCHITECTURE.md §5, §8)
-- ---------------------------------------------------------------------------
CREATE TABLE country_scores (
  iso3                char(3) NOT NULL CHECK (iso3 ~ '^[A-Z]{3}$'),
  score_class         score_class NOT NULL,
  computed_at         timestamptz NOT NULL DEFAULT now(),
  methodology_version text NOT NULL,
  weight_vector_hash  text NOT NULL,
  cycle_date          date NOT NULL,
  shadow              boolean NOT NULL DEFAULT false,  -- §8 shadow backfill, never "the record"
  payload             jsonb NOT NULL,                  -- the class's fragment of the country record
  PRIMARY KEY (iso3, score_class, computed_at, methodology_version)
);
CREATE INDEX idx_scores_cycle ON country_scores (cycle_date, score_class);
CALL make_append_only('country_scores');

-- Current published state = latest non-shadow row per (iso3, class).
CREATE VIEW latest_country_scores AS
SELECT DISTINCT ON (iso3, score_class)
       iso3, score_class, computed_at, methodology_version,
       weight_vector_hash, cycle_date, payload
FROM country_scores
WHERE NOT shadow
ORDER BY iso3, score_class, computed_at DESC;

-- One row per publication cycle (weekly or out-of-cycle).
CREATE TABLE publications (
  cycle_date          date NOT NULL,
  generated_at        timestamptz NOT NULL DEFAULT now(),
  methodology_version text NOT NULL,
  weight_vector_hash  text NOT NULL,
  out_of_cycle        boolean NOT NULL DEFAULT false,
  trigger_event_id    text REFERENCES events(event_id),  -- the Tier-4/5 trigger, if out-of-cycle
  artifact_paths      jsonb NOT NULL,
  PRIMARY KEY (cycle_date, generated_at)
);
CALL make_append_only('publications');

-- ---------------------------------------------------------------------------
-- 8. Research gaps (mutable status) and audit log (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE research_gaps (
  gap_id       bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  country_iso3 char(3) CHECK (country_iso3 IS NULL OR country_iso3 ~ '^[A-Z]{3}$'),
  layer        stack_layer,
  description  text NOT NULL,
  origin       text,                     -- e.g. 'dead_feed:<feed_id>', 'analyst'
  status       gap_status NOT NULL DEFAULT 'open',
  opened_at    timestamptz NOT NULL DEFAULT now(),
  closed_at    timestamptz
);

CREATE TABLE audit_log (
  audit_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  actor       text NOT NULL,
  action      text NOT NULL,
  object_type text NOT NULL,
  object_id   text NOT NULL,
  detail      jsonb,
  at          timestamptz NOT NULL DEFAULT now()
);
CALL make_append_only('audit_log');

-- ---------------------------------------------------------------------------
-- 9. Roles (ARCHITECTURE.md §5, §12): the public API role physically cannot write
-- ---------------------------------------------------------------------------
DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sd_pipeline') THEN
    CREATE ROLE sd_pipeline NOLOGIN;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'sd_public_ro') THEN
    CREATE ROLE sd_public_ro NOLOGIN;
  END IF;
END $$;

GRANT USAGE ON SCHEMA public TO sd_pipeline, sd_public_ro;

-- Pipeline/admin: insert everywhere, update only the genuinely mutable tables.
GRANT SELECT, INSERT ON ALL TABLES IN SCHEMA public TO sd_pipeline;
GRANT UPDATE ON feeds, review_queue, research_gaps TO sd_pipeline;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO sd_pipeline;

-- Public API: read-only, and only over published surfaces — no raw archive,
-- no review queue, no feed config, no audit trail.
GRANT SELECT ON events, event_sources, sources,
                exposure_edges, cascade_edges,
                country_scores, latest_country_scores,
                ledger, ledger_resolutions, exercise_observations,
                test_sets, renewal_cliffs, publications
TO sd_public_ro;

COMMIT;
