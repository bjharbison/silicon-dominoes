-- ============================================================================
-- Silicon Dominoes — schema guarantee tests
-- Run AFTER schema.sql on a fresh database:
--   psql "$DB_URL" -v ON_ERROR_STOP=1 -f test_schema.sql
-- Every test prints PASS via RAISE NOTICE; any failure aborts the script.
-- Pattern: statements that MUST fail run inside DO blocks that catch the
-- expected error (matched by message) and re-raise anything unexpected.
-- Deferred constraint triggers are forced with SET CONSTRAINTS ALL IMMEDIATE
-- so their firing is observable inside a block.
-- ============================================================================

\set ON_ERROR_STOP 1

BEGIN;

-- ---------- fixtures: feed, capture, sources -------------------------------
INSERT INTO feeds (feed_id, feed_class, url)
VALUES ('feed-test-tenders', 'structured_data', 'https://example.test/tenders');

INSERT INTO raw_captures (feed_id, retrieved_at, url, sha256, object_key, snapshot_id, snapshot_url)
VALUES ('feed-test-tenders', now(), 'https://example.test/award/1',
        repeat('a', 64), 'raw/aa/test1', 'snap-1', 'https://archive.test/snap-1');

INSERT INTO sources (url, archived_url, snapshot_id, retrieved_at, source_tier, publisher, title)
VALUES ('https://example.test/award/1',  'https://archive.test/snap-1', 'snap-1', now(), 'S1', 'Test PPRA', 'Award notice 1'),
       ('https://example.test/press/1',  'https://archive.test/snap-2', 'snap-2', now(), 'S2', 'Test Trade Press', 'Award coverage'),
       ('https://example.test/blog/1',   'https://archive.test/snap-3', 'snap-3', now(), 'S5', 'Random Blog', 'Rumor'),
       ('https://example.test/state/1',  'https://archive.test/snap-4', 'snap-4', now(), 'S4', 'State Outlet', 'Announcement');

-- ---------- T1: properly corroborated Tier-3 event commits -----------------
INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                    instrument_tier, direction, depth_dimensions, summary,
                    confidence, methodology_version, approved_by)
VALUES ('ev-test-1', current_date, now(), 'KEN', 'cloud', 3, 'us',
        ARRAY['T','E'], 'Signed three-year sovereign-cloud hosting award, value and counterparty disclosed.',
        0.90, 'v2.0', 'test-reviewer');
INSERT INTO event_sources SELECT 'ev-test-1', source_id FROM sources WHERE source_tier IN ('S1','S2');

DO $$ BEGIN
  EXECUTE 'SET CONSTRAINTS ALL IMMEDIATE';
  RAISE NOTICE 'PASS T1: corroborated Tier-3 event accepted';
END $$;

-- ---------- T2: Tier-3 event with only an S4 source is rejected ------------
SET CONSTRAINTS ALL DEFERRED;
DO $$ BEGIN
  INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                      instrument_tier, direction, depth_dimensions, summary,
                      confidence, methodology_version, approved_by)
  VALUES ('ev-test-2', current_date, now(), 'KEN', 'networks', 3, 'prc',
          ARRAY['T'], 'Announced RAN contract award reported only by a state outlet.',
          0.50, 'v2.0', 'test-reviewer');
  INSERT INTO event_sources SELECT 'ev-test-2', source_id FROM sources WHERE source_tier = 'S4';
  EXECUTE 'SET CONSTRAINTS ALL IMMEDIATE';
  RAISE EXCEPTION 'T2 FAILED: undercorroborated Tier-3 event was accepted';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%corroboration rule%' THEN
    RAISE NOTICE 'PASS T2: undercorroborated Tier-3 event rejected';
  ELSE RAISE; END IF;
END $$;

-- ---------- T3: S5-only event is rejected at any tier ----------------------
SET CONSTRAINTS ALL DEFERRED;
DO $$ BEGIN
  INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                      instrument_tier, direction, depth_dimensions, summary,
                      confidence, methodology_version, approved_by)
  VALUES ('ev-test-3', current_date, now(), 'KEN', 'models', 1,'us',
          ARRAY['T'], 'Rumored partnership sourced only from an unattributed blog.',
          0.20, 'v2.0', 'test-reviewer');
  INSERT INTO event_sources SELECT 'ev-test-3', source_id FROM sources WHERE source_tier = 'S5';
  EXECUTE 'SET CONSTRAINTS ALL IMMEDIATE';
  RAISE EXCEPTION 'T3 FAILED: S5-only event was accepted';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%S5 is never scored%' THEN
    RAISE NOTICE 'PASS T3: S5-only event rejected';
  ELSE RAISE; END IF;
END $$;

-- ---------- T4: UPDATE on a scored event is rejected ------------------------
DO $$ BEGIN
  UPDATE events SET summary = 'edited history' WHERE event_id = 'ev-test-1';
  RAISE EXCEPTION 'T4 FAILED: UPDATE on events succeeded';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%append-only violation%' THEN
    RAISE NOTICE 'PASS T4: UPDATE on events rejected';
  ELSE RAISE; END IF;
END $$;

-- ---------- T5: DELETE on a scored event is rejected ------------------------
DO $$ BEGIN
  DELETE FROM events WHERE event_id = 'ev-test-1';
  RAISE EXCEPTION 'T5 FAILED: DELETE on events succeeded';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%append-only violation%' THEN
    RAISE NOTICE 'PASS T5: DELETE on events rejected';
  ELSE RAISE; END IF;
END $$;

-- ---------- T6: correction appends and must reference its target ------------
DO $$ BEGIN
  INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                      instrument_tier, direction, depth_dimensions, summary,
                      confidence, methodology_version, approved_by, correction)
  VALUES ('ev-test-4', current_date, now(), 'KEN', 'cloud', 1, 'us',
          ARRAY['T'], 'Correction attempt with no corrects_event_id reference.',
          0.80, 'v2.0', 'test-reviewer', true);
  RAISE EXCEPTION 'T6a FAILED: correction without reference was accepted';
EXCEPTION WHEN check_violation THEN
  RAISE NOTICE 'PASS T6a: correction without corrects_event_id rejected';
END $$;

SET CONSTRAINTS ALL DEFERRED;
INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                    instrument_tier, direction, depth_dimensions, summary,
                    confidence, methodology_version, approved_by,
                    correction, corrects_event_id)
VALUES ('ev-test-4', current_date, now(), 'KEN', 'cloud', 1, 'us',
        ARRAY['T'], 'Corrected verified value for the cloud award after audited figure published.',
        0.85, 'v2.0', 'test-reviewer', true, 'ev-test-1');
INSERT INTO event_sources SELECT 'ev-test-4', source_id FROM sources WHERE source_tier = 'S1';
DO $$ BEGIN
  EXECUTE 'SET CONSTRAINTS ALL IMMEDIATE';
  RAISE NOTICE 'PASS T6b: correction row appended with reference to ev-test-1';
END $$;

-- ---------- T7: reversal must name target and reversed tier -----------------
DO $$ BEGIN
  INSERT INTO events (event_id, event_date, retrieved_at, country_iso3, layer,
                      instrument_tier, direction, depth_dimensions, summary,
                      confidence, methodology_version, approved_by)
  VALUES ('ev-test-5', current_date, now(), 'KEN', 'networks', 4, 'reversal',
          ARRAY['T'], 'Contract cancellation reported without reversal fields set.',
          0.80, 'v2.0', 'test-reviewer');
  RAISE EXCEPTION 'T7 FAILED: reversal without target/tier was accepted';
EXCEPTION WHEN check_violation THEN
  RAISE NOTICE 'PASS T7: reversal without reversal_target/reversed_tier rejected';
END $$;

-- ---------- T8: temporal country_scores + latest view ------------------------
INSERT INTO country_scores (iso3, score_class, computed_at, methodology_version,
                            weight_vector_hash, cycle_date, payload)
VALUES ('KEN','T', now() - interval '7 days', 'v2.0', 'sha256:3f9a1c',
        current_date - 7, '{"t_net": 1.1}'),
       ('KEN','T', now(),                     'v2.0', 'sha256:3f9a1c',
        current_date,     '{"t_net": 2.6}');

DO $$
DECLARE v numeric;
BEGIN
  SELECT (payload->>'t_net')::numeric INTO v
  FROM latest_country_scores WHERE iso3='KEN' AND score_class='T';
  IF v = 2.6 THEN RAISE NOTICE 'PASS T8a: latest_country_scores returns newest row';
  ELSE RAISE EXCEPTION 'T8a FAILED: latest view returned %', v; END IF;
END $$;

INSERT INTO country_scores (iso3, score_class, computed_at, methodology_version,
                            weight_vector_hash, cycle_date, shadow, payload)
VALUES ('KEN','T', now() + interval '1 second', 'v2.1', 'sha256:zzzz',
        current_date, true, '{"t_net": 9.9}');
DO $$
DECLARE v numeric;
BEGIN
  SELECT (payload->>'t_net')::numeric INTO v
  FROM latest_country_scores WHERE iso3='KEN' AND score_class='T';
  IF v = 2.6 THEN RAISE NOTICE 'PASS T8b: shadow rows excluded from the published view';
  ELSE RAISE EXCEPTION 'T8b FAILED: shadow row leaked into latest view (%)', v; END IF;
END $$;

DO $$ BEGIN
  UPDATE country_scores SET payload = '{"t_net": 0}' WHERE iso3='KEN';
  RAISE EXCEPTION 'T8c FAILED: UPDATE on country_scores succeeded';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%append-only violation%' THEN
    RAISE NOTICE 'PASS T8c: country_scores is append-only';
  ELSE RAISE; END IF;
END $$;

-- ---------- T9: ledger append-only; resolution is its own row ----------------
INSERT INTO test_sets VALUES ('ts-test-Q3', now(), true);
INSERT INTO renewal_cliffs (cliff_id, test_set_id, country_iso3, layer, cliff_type,
                            expected_decision_date, incumbent_pole)
VALUES ('cliff-test-1', 'ts-test-Q3', 'KEN', 'networks', 'ran_contract',
        current_date + 90, 'prc');

INSERT INTO ledger (prediction_id, class, methodology_version, resolution_criterion,
                    resolution_date, cliff_id, test_set_id, forecast_source,
                    derivation_version, distribution)
VALUES ('pred-test-1', 'index', 'v2.0',
        'Award record on the national e-procurement portal (S1) or two independent S2/S3 reports.',
        current_date + 120, 'cliff-test-1', 'ts-test-Q3', 'model', 'deriv-v2.0.0',
        '{"incumbent_retained":0.5,"switch_us":0.15,"switch_prc":0.05,"switch_sovereign":0.05,"split_award":0.2,"cancellation_delay":0.05}');

DO $$ BEGIN
  UPDATE ledger SET probability = 0.9 WHERE prediction_id = 'pred-test-1';
  RAISE EXCEPTION 'T9a FAILED: UPDATE on ledger succeeded';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%append-only violation%' THEN
    RAISE NOTICE 'PASS T9a: ledger rows cannot be edited';
  ELSE RAISE; END IF;
END $$;

INSERT INTO ledger_resolutions (prediction_id, outcome, brier_contribution, evidence_refs)
VALUES ('pred-test-1', 'incumbent_retained', 0.18, ARRAY['ev-test-1']);
DO $$ BEGIN RAISE NOTICE 'PASS T9b: resolution recorded as its own append-only row'; END $$;

-- ---------- T10: cascade edges need a ledger entry and a real mechanism ------
INSERT INTO ledger (prediction_id, class, methodology_version, resolution_criterion,
                    resolution_date, country_iso3, claim, mechanism, probability)
VALUES ('pred-test-2', 'cascade', 'v2.0',
        'Award notice on the target country e-procurement portal naming the vendor.',
        current_date + 200, 'TZA',
        'Tanzania will award its e-government hosting tender to a US-pole provider by the stated date.',
        'Reference deployment channel: the Kenyan award is cited as benchmark in the published pre-tender consultation.',
        0.35);

DO $$ BEGIN
  INSERT INTO cascade_edges (edge_id, cycle_date, from_iso3, to_iso3, mechanism,
                             mechanism_channel, probability, ledger_ref, methodology_version)
  VALUES ('casc-test-bad', current_date, 'KEN', 'TZA',
          'These neighbors correlate strongly in vendor choice across the region.',
          'reference_deployment', 0.4, 'pred-test-2', 'v2.0');
  RAISE EXCEPTION 'T10a FAILED: lazy mechanism string was accepted';
EXCEPTION WHEN check_violation THEN
  RAISE NOTICE 'PASS T10a: "neighbors correlate" mechanism rejected';
END $$;

DO $$ BEGIN
  INSERT INTO cascade_edges (edge_id, cycle_date, from_iso3, to_iso3, mechanism,
                             mechanism_channel, probability, ledger_ref, methodology_version)
  VALUES ('casc-test-orphan', current_date, 'KEN', 'TZA',
          'Reference deployment channel: benchmark citation in the published pre-tender consultation.',
          'reference_deployment', 0.4, 'pred-does-not-exist', 'v2.0');
  RAISE EXCEPTION 'T10b FAILED: cascade edge without ledger entry was accepted';
EXCEPTION WHEN foreign_key_violation THEN
  RAISE NOTICE 'PASS T10b: cascade edge requires an existing ledger entry';
END $$;

INSERT INTO cascade_edges (edge_id, cycle_date, from_iso3, to_iso3, mechanism,
                           mechanism_channel, probability, ledger_ref, methodology_version)
VALUES ('casc-test-good', current_date, 'KEN', 'TZA',
        'Reference deployment channel: the Kenyan award is cited as benchmark in the published pre-tender consultation.',
        'reference_deployment', 0.35, 'pred-test-2', 'v2.0');
DO $$ BEGIN RAISE NOTICE 'PASS T10c: well-formed cascade edge accepted'; END $$;

-- ---------- T11: frozen test sets cannot be edited ---------------------------
DO $$ BEGIN
  DELETE FROM renewal_cliffs WHERE cliff_id = 'cliff-test-1';
  RAISE EXCEPTION 'T11 FAILED: DELETE on renewal_cliffs succeeded';
EXCEPTION WHEN raise_exception THEN
  IF SQLERRM LIKE '%append-only violation%' THEN
    RAISE NOTICE 'PASS T11: frozen test-set cliffs cannot be deleted';
  ELSE RAISE; END IF;
END $$;

ROLLBACK;  -- tests leave no residue; run repeatably

\echo ''
\echo 'All schema guarantee tests passed (transaction rolled back — database unchanged).'
