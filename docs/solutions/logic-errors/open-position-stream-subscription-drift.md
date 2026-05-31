---
title: Subscribe open position tokens even after market discovery rolls forward
date: 2026-05-28
category: logic-errors
module: weather_bot.live_paper_runner
problem_type: logic_error
component: service_object
severity: medium
symptoms:
  - "A held Seoul May 28 NO position showed mark 0.65 on the dashboard while the live CLOB NO book was near 0.98/0.99."
  - "The bot logged repeated `no websocket orderbook snapshot` mark errors for held May 28 positions."
  - "The WebSocket status counted only discovery markets, while older held positions were still open."
root_cause: logic_error
resolution_type: code_fix
tags: [paper-trading, orderbook-stream, dashboard, open-positions, mark-price]
---

# Subscribe open position tokens even after market discovery rolls forward

## Problem

The real-time runner subscribed only to tokens from the current discovery set. When discovery rolled forward to May 29 markets, still-held May 28 positions could fall out of the WebSocket subscription. Their `last_mark_price` then stayed stale in `paper_state.json`, so the dashboard disagreed with the live Polymarket order book.

## Symptoms

- Dashboard mark remained at an old value.
- Direct CLOB lookup for the held token showed a very different bid/ask.
- Service logs repeated mark errors like `no websocket orderbook snapshot for token ...`.
- Restarting the dashboard did not fix the mark because the stale value came from the bot state file.

## What Didn't Work

Checking only the dashboard API was insufficient because it faithfully rendered `paper_state.json`. The root cause was upstream in the runner subscription set, not in the dashboard renderer.

## Solution

Build the stream registry from both:

- current discovered weather markets
- all currently open paper positions, hydrated by market id or reconstructed from the position

Then subscribe every token in that combined registry. The stream status should count the combined market/token set, so a jump from 41 markets to 46 markets is expected when 5 held markets are outside current discovery.

## Why This Works

Open positions remain economically active even after discovery moves to newer market dates. Their marks, take-profit checks, stop checks, and dashboard PnL must keep using live order books until the position is closed or settled.

## Prevention

- Treat open position tokens as mandatory stream subscriptions.
- When dashboard prices disagree with Polymarket, compare `paper_state.json` against direct CLOB book lookup for the held `token_id`.
- Search recent service logs for `no websocket orderbook snapshot` before assuming the market price itself is wrong.
