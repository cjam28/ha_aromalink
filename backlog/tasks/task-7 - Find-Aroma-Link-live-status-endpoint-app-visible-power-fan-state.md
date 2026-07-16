---
id: TASK-7
title: Find Aroma-Link live-status endpoint (app-visible power/fan state)
status: To Do
assignee: []
created_date: '2026-07-15 21:56'
labels:
  - aroma-link
  - v3
  - api
dependencies: []
priority: medium
ordinal: 7000
---

## Description

<!-- SECTION:DESCRIPTION:BEGIN -->
The cloud deviceInfo endpoint (/device/deviceInfo/{id}) now returns metadata only — no onOff/fan/workStatus/workRemainTime fields. v3.0.4 works around it by carrying the last known state forward (fixes the 206s power-flap loop), but the consequence is HA cannot see changes made in the Aroma-Link app (e.g. fan turned on in-app reads off in HA). The mobile app clearly has a live-status source (it shows a running pause countdown). Capture the app's API traffic (mitmproxy or similar) to find the live-status endpoint, then add it to AromaLinkDeviceCoordinator as the poll source for state/fan/workStatus/remain times, restoring truthful external-state visibility. Also re-verify workStatus 1/2 semantics on live hardware while at it.
<!-- SECTION:DESCRIPTION:END -->
