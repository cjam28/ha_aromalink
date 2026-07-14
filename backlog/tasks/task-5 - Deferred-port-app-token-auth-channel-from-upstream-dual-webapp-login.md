---
id: TASK-5
title: 'Deferred port: app-token auth channel from upstream (dual web+app login)'
status: Deferred
assignee: []
created_date: '2026-07-14 23:52'
labels:
  - convergence
  - upstream-harvest
dependencies: []
priority: low
ordinal: 5000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
Upstream's AromaLinkAuthCoordinator has a second auth path (MD5 app login, /v2/app/token, refresh on expiry code 13002, newWork/newSwitch endpoints) that survives web JSESSIONID login outages. Port only if the fork starts seeing web-login failures in Cauldron logs — the fork's SSL fallback + in-cycle 401/403 retry cover current failure modes. Reference: upstream AromaLinkAuthCoordinator.py L88-L452.
<!-- SECTION:DESCRIPTION:END -->
