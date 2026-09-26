# Plan 9 — AI Features (OpenRouter)

- **Depends on:** plan-1 (worker for batch calls); uses the phase-2 plan-2
      production images unchanged
- **Goal:** turn the phase-2 plan-2 plumbing into three genuinely useful,
      always-opt-in AI features. Everything degrades to a clean disabled state
      without `OPENROUTER_API_KEY`.

## 1. Features

- [ ] **Bulk product descriptions:** "Generate missing descriptions" action on
      the product list — arq job iterates products without descriptions (limit
      + rate-limited), writes drafts into a review queue, not straight onto the
      product. Review page: accept / edit / discard per product.
- [ ] **Report summarizer:** on Dashboard and Reports, "Summarize" sends the
      server-side aggregate JSON (sales, aging, tax) to the configured model
      and renders a short narrative with 2–3 observations. Cached per report +
      period; explicitly labeled AI-generated.
- [ ] **Semantic document search (pgvector):** nightly job embeds
      products/contacts/invoice memos (text-embedding model via OpenRouter);
      `pgvector` cosine search powers the header global search with a
      "Semantic matches" section. Extension is already enabled since phase-1
      migration 0001.

## 2. Guardrails

- [ ] All AI calls go through one service module with: model from env, token
      budget per day (redis counter), request timeout, and structured logging
      (no payload bodies logged, only sizes/latency).
- [ ] AI output is never auto-saved: review queue for bulk descriptions,
      client-side accept for summaries. No customer data leaves the system
      except the explicit prompt payloads listed above.
- [ ] Feature flag `AI_ENABLED` global kill switch on top of the key check.

## Acceptance

- [ ] With a key: bulk generation fills the review queue; summary renders on
      dashboard; semantic search returns relevant hits for typos/synonyms.
- [ ] Without a key or over budget: buttons show clean disabled states; worker
      logs reasons; nothing breaks.
- [ ] Tests: queue/review flow in inline mode, budget enforcement, embedder
      fallback; `make verify` green.
