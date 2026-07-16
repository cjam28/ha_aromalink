---
id: TASK-7
title: 'Card: version-stamp module imports so updates don''t require hard refresh'
status: To Do
assignee: []
created_date: '2026-07-16 00:50'
labels:
  - aroma-link
  - v3
  - card
dependencies: []
priority: low
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The card's cache-buster (?v=hash) applies only to the entry resource URL; sub-module imports (./al-model.js etc.) are bare specifiers the browser caches heuristically. After a card update, a fresh entry can pair with stale cached modules (e.g. importing removeWindows from an old al-model.js) producing a Lovelace 'config error' until the user hard-refreshes — standard HACS-card annoyance, but fixable: add a release step that stamps a ?v=<version> query onto every relative import specifier in www/*.js (simple sed in a release script or pre-commit), so bumping the manifest version busts the whole module graph atomically.
<!-- SECTION:DESCRIPTION:END -->
