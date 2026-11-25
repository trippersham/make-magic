-- Backlog Steward — derived flow-metric views over the append-only flow_event log.
--
-- NO AGENT CLOCK: every duration is computed here, at query time, from the
-- immutable timestamps in flow_event. Interval SQL per research/metrics-data-layer.md.
--
-- `steward_now()` is the reference "now" for open intervals / aging. It defaults
-- to the wall clock; tests (and what-if queries) override it with
-- `CREATE OR REPLACE MACRO steward_now() AS TIMESTAMP '...'` for determinism.
--
-- "First active-work state" for cycle time is 'In Progress' (design §13 constraint 4 /
-- research open-q 3). This belongs in .claude/steward.yml; hardcoded here as the v1
-- default until the config is wired (Phase 0.2). Change in ONE place: cycle_time below.

CREATE OR REPLACE MACRO steward_now() AS CAST(now() AS TIMESTAMP);

-- Status intervals: each 'status' event opens an interval of that state lasting
-- until the next status event for the same item (LEAD), or steward_now() if open.
CREATE OR REPLACE VIEW status_interval AS
SELECT
    item_node_id,
    card_id,
    to_state AS state,
    event_ts AS state_start,
    LEAD(event_ts) OVER (PARTITION BY item_node_id ORDER BY event_ts) AS state_end,
    LEAD(event_ts) OVER (PARTITION BY item_node_id ORDER BY event_ts) IS NULL AS is_current
FROM flow_event
WHERE kind = 'status';

-- Raw pause intervals: pair each 'added' of blocked/needs-info with the NEXT
-- event for that (item,label); if none follows, the pause runs to steward_now().
CREATE OR REPLACE VIEW blocked_interval AS
WITH lbl AS (
    SELECT
        item_node_id,
        label,
        label_action,
        event_ts,
        LEAD(event_ts) OVER (PARTITION BY item_node_id, label ORDER BY event_ts) AS next_ts
    FROM flow_event
    WHERE kind = 'label' AND label IN ('blocked', 'needs-info')
)
SELECT
    item_node_id,
    event_ts AS blk_start,
    COALESCE(next_ts, steward_now()) AS blk_end
FROM lbl
WHERE label_action = 'added';

-- Merge overlapping/adjacent pause intervals per item into disjoint intervals
-- (gaps-and-islands) so blocked+needs-info overlap is a union, not double-counted.
CREATE OR REPLACE VIEW blocked_merged AS
WITH ordered AS (
    SELECT
        item_node_id,
        blk_start AS s,
        blk_end AS e,
        MAX(blk_end) OVER (
            PARTITION BY item_node_id ORDER BY blk_start
            ROWS BETWEEN UNBOUNDED PRECEDING AND 1 PRECEDING
        ) AS prev_max_e
    FROM blocked_interval
),
grp AS (
    SELECT
        item_node_id, s, e,
        SUM(CASE WHEN prev_max_e IS NULL OR s > prev_max_e THEN 1 ELSE 0 END)
            OVER (PARTITION BY item_node_id ORDER BY s) AS g
    FROM ordered
)
SELECT item_node_id, MIN(s) AS blk_start, MAX(e) AS blk_end
FROM grp
GROUP BY item_node_id, g;

-- Seconds of pause overlapping an arbitrary [lo, hi) window for one item.
CREATE OR REPLACE MACRO blocked_overlap_secs(p_item, p_lo, p_hi) AS (
    SELECT COALESCE(SUM(
        GREATEST(0, EXTRACT(EPOCH FROM (
            LEAST(p_hi, b.blk_end) - GREATEST(p_lo, b.blk_start)
        )))
    ), 0)
    FROM blocked_merged b
    WHERE b.item_node_id = p_item
);

-- Time in state, gross and NET (minus the union of overlapping pause intervals).
CREATE OR REPLACE VIEW time_in_state AS
SELECT
    si.item_node_id,
    si.card_id,
    si.state,
    si.state_start,
    COALESCE(si.state_end, steward_now()) AS state_end,
    si.is_current,
    EXTRACT(EPOCH FROM (COALESCE(si.state_end, steward_now()) - si.state_start)) AS gross_secs,
    EXTRACT(EPOCH FROM (COALESCE(si.state_end, steward_now()) - si.state_start))
        - blocked_overlap_secs(si.item_node_id, si.state_start, COALESCE(si.state_end, steward_now()))
        AS net_secs
FROM status_interval si;

-- Aging WIP: the active seconds accrued in the CURRENT (still-open) state.
CREATE OR REPLACE VIEW aging_wip AS
SELECT item_node_id, card_id, state, state_start, gross_secs, net_secs
FROM time_in_state
WHERE is_current AND state <> 'Done';

-- Cycle time: first entry into the active-work state -> first Done, minus pauses.
CREATE OR REPLACE VIEW cycle_time AS
WITH active_start AS (
    SELECT item_node_id, MIN(event_ts) AS t0
    FROM flow_event
    WHERE kind = 'status' AND to_state = 'In Progress'  -- config: steward.yml active state
    GROUP BY item_node_id
),
done AS (
    SELECT item_node_id, MIN(event_ts) AS t1
    FROM flow_event
    WHERE kind = 'status' AND to_state = 'Done'
    GROUP BY item_node_id
)
SELECT
    a.item_node_id,
    a.t0,
    d.t1,
    EXTRACT(EPOCH FROM (d.t1 - a.t0)) AS gross_secs,
    EXTRACT(EPOCH FROM (d.t1 - a.t0)) - blocked_overlap_secs(a.item_node_id, a.t0, d.t1) AS net_secs
FROM active_start a
JOIN done d USING (item_node_id);

-- Lead time: created -> first Done (calendar, no pause subtraction).
CREATE OR REPLACE VIEW lead_time AS
WITH created AS (
    SELECT item_node_id, MIN(event_ts) AS t0
    FROM flow_event WHERE kind = 'created' GROUP BY item_node_id
),
done AS (
    SELECT item_node_id, MIN(event_ts) AS t1
    FROM flow_event WHERE kind = 'status' AND to_state = 'Done' GROUP BY item_node_id
)
SELECT c.item_node_id, c.t0, d.t1, EXTRACT(EPOCH FROM (d.t1 - c.t0)) AS lead_secs
FROM created c
JOIN done d USING (item_node_id);

-- Throughput: count of items reaching Done per ISO week.
CREATE OR REPLACE VIEW throughput AS
SELECT date_trunc('week', event_ts) AS week, COUNT(*) AS done_count
FROM flow_event
WHERE kind = 'status' AND to_state = 'Done'
GROUP BY 1
ORDER BY 1;

-- Cumulative flow: day-clipped count of items in each state, for a CFD chart.
CREATE OR REPLACE VIEW cfd AS
WITH spine AS (
    SELECT CAST(d AS DATE) AS day
    FROM range(
        (SELECT date_trunc('day', MIN(event_ts)) FROM flow_event),
        (SELECT date_trunc('day', steward_now())) + INTERVAL 1 DAY,
        INTERVAL 1 DAY
    ) t(d)
)
SELECT sp.day, si.state, COUNT(*) AS cards
FROM spine sp
JOIN status_interval si
    ON sp.day >= CAST(si.state_start AS DATE)
   AND sp.day < CAST(COALESCE(si.state_end, steward_now()) AS DATE) + INTERVAL 1 DAY
GROUP BY sp.day, si.state
ORDER BY sp.day, si.state;
