# Automation Coding Standards (starter — replace with your team's standards)

Language-specific rules come from `language-packs/<pack>/conventions.md`. These apply to every language.

- **Structure:** feature files hold behaviour, step definitions hold glue only (thin: parse, delegate, assert), page objects/screens/API clients hold interactions, and builders/factories hold data.
- **Naming:** intention-revealing names. Classes are nouns (`CartPage`, `CouponApiClient`), methods are verbs (`applyCoupon`), and step methods mirror step text.
- **No duplication:** search before adding. Two step patterns that match the same text are a bug.
- **Synchronisation:** explicit waits on conditions only. No fixed sleeps.
- **Locators** (UI): prefer stable test ids and accessible roles/labels, then CSS. Avoid absolute XPath and text that changes with copy edits.
- **Assertions:** one logical assertion per `Then`, with messages that explain the business expectation. Use the repo's assertion library.
- **State:** share scenario state through the repo's context/DI mechanism, never through static mutable fields.
- **Config and secrets:** environment URLs, users and secrets come from configuration or the secret mechanism, never literals.
- **Logging:** use the repo's logger. No stray prints or debug statements.
- **Comments:** explain *why*, not *what*. No commented-out code. No TODOs without a work item.
- **Formatting:** match the surrounding code (indentation, braces, import order, line endings).
