# Graph UI Review Notes

## Purpose

This note summarizes the local Graph UI polish added after the first E-task handoff. It is intended for PR review and team synchronization. The changes are frontend-only unless explicitly noted, and they do not change the Feishu interface contract.

## What Changed

- Improved node label readability while keeping the original circular/ring node visual style.
- Added Graph zoom controls and a fit-view control beside the existing graph canvas.
- Added layout controls for force-directed graph tuning:
  - repulsion
  - attraction
  - link distance
  - node spacing
  - compact / balanced / open presets
- Added deterministic initial node positioning so unrelated nodes are scattered inside the visible canvas instead of stacking in the center.
- Added five local demo graph skills and weighted demo edges so the layout can be reviewed when real graph relation data is still sparse.
- Tuned edge labels:
  - relationship text is displayed beside the arrow, not directly on the line.
  - labels use a format such as `depends on · 0.85`.
  - relationship labels appear when zoomed in and disappear when zoomed out.

## Review Path

Use this route for the most complete local review:

```text
http://127.0.0.1:5173/graph?skill_id=test_graph_design_plan
```

Suggested checks:

- Confirm the five-node test subgraph loads.
- Click nodes and confirm the right-side detail panel still works.
- Open layout settings and adjust repulsion, attraction, link distance, and node spacing.
- Zoom in and confirm edge relationship labels appear beside arrows.
- Zoom out and confirm edge relationship labels disappear.
- Click "return full graph" and confirm the full graph still loads.

## Interface Impact

- No Feishu contract changes.
- No new frontend dependency.
- No new required backend API.
- Existing `/graph` and `/graph/subgraph` usage is preserved.
- The demo graph seed data is only for local review while real relation data is limited.

## Validation

Latest validation completed locally:

```bash
npm run build
git diff --check
python -m compileall -q skillos\api
python -m pytest skillos\tests\test_layers.py -q
```

Browser smoke also passed for:

```text
/graph
/graph?skill_id=test_graph_design_plan
```

The browser console showed no runtime errors during the Graph smoke. The known local WebSocket warning remains non-blocking and is not introduced by these Graph UI changes.

## Notes For Reviewer

- The Graph UI changes are meant to improve reviewability and demo stability, not to redefine the final visual design.
- Real graph richness still depends on A/D or backend relation data. The seeded test graph exists so layout behavior can be inspected before real relation edges are complete.
- Current PR can be reviewed as frontend/API/pipeline readiness plus Graph demo polish. If the team wants Graph demo seed data kept out of `main`, it can be split or removed in a follow-up commit.
