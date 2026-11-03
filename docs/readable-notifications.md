# Readable descriptions, digests and release notes (beta.8)

Descriptions no longer truncate in creation_embed, make_embed or media_embeds. The shared message_pages path converts Markdown/media first, then splits complete text into UTF-16-safe description cards. Code fences reopen across boundaries. First-page metadata is kept once, images stay in vertical pages and the source timestamp stays on the final segment. The existing durable per-source edit mapping handles growing and shrinking text without replacing unrelated comments. Discord limits still apply per card and message; the whole content is split rather than cut.

Daily and weekly PR lists link each displayed PR to the canonical repository /pull/number path, just like the issue section. Long linked lists use continuation cards/messages with stable per-period receipts. Digest headings remain headings rather than accidentally using a PR title.

The CI preview uses one JavaScript elapsed-time clock for all five checks and the running/success state. Completion classes are applied to HTML rows rather than animating SVG opacity independently. Pause/resume, visibility, selection/replay and reduced-motion share this clock. The no-JavaScript fallback is a static completed example. Chromium and WebKit rendering checks are separate from actual iPhone validation.

All historical versions retain their labels and ordering, with one to three concise bilingual user-facing highlights each. Operational implementation details are kept in repository docs rather than public release notes. The known new-thread sound limitation is stated without claiming the experimental flag worked.
