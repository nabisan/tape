# tape: design system

The run view is a desk tool: it uses the whole window and shows as much as can be read at once. Space is spent on legible rows, not on margins.

## Rules

- Full width. Outer margin 24px, gutter 24px; no centred column, no max width.
- One face: JetBrains Mono. Body 14px, tables 12px, headline figures 20px, ticker 28px.
- Rows: 24px in HTML tables (performance, monthly), 28px in data grids (blotter, orders, sweeps).
- Square corners everywhere. No cards, no shadows; sections are separated by one hairline.
- Colour: black, white and greys only. Pastel green and red appear only inside charts.

## Layout

| Band | Content |
|---|---|
| Header | ticker and period left; six headline figures right; strategy, execution and run identifiers on full-width lines below |
| Equity, Performance | equity and drawdown chart (7 of 12) beside the four metric groups in two columns (5 of 12) |
| Price | candles, indicators and fills, full width; window slider below |
| Blotter | trades and orders tabs, full width |
| Monthly returns | full width |

## Colour

| Role | Value |
|---|---|
| Background | `#ffffff` |
| Ink | `#111111` |
| Secondary | `#555555` |
| Muted | `#8c8c8c` |
| Hairline | `#e3e3e3` |
| Chart up | `#8cc5a0` |
| Chart down, drawdown | `#e7a3a3` |

Buy fills are filled black triangles, sell fills hollow ones. Strategy B in a comparison is drawn in muted grey, dashed.
