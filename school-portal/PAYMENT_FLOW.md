# Student payment flow

LionTech uses Flutterwave for every country or territory in UN M49 Africa (002), and Stripe for every other known residence. This is the academy's merchant policy, not a statement about Stripe's global capabilities. Unknown residence requires a country selection. Country corrections use the existing authenticated locale preferences and update local-currency quotes before checkout.

Editable desktop, mobile, and status designs: https://www.figma.com/design/KEjpn4ZX7yaYecgGf6nZ2F?node-id=90-177

## Student experience

1. Review the program, student number, payment stage, local amount, and due date.
2. Confirm residence. Display exactly one assigned provider with its existing brand asset.
3. Continue to that provider's hosted checkout. Card details stay with the provider. Flutterwave may offer eligible local options inside its own checkout; the portal offers no standalone bank deposit, e-transfer, cash, Visa, or manually arranged wallet payment.
4. Show payment activity and the saved reference. A return URL alone never grants access: the server verifies the provider transaction, amount, currency, and enrollment reference.
5. Credit the saved payment stage once. Existing enrollment approvals, access holds, rejections, and suspensions remain authoritative.

## Availability and recovery

Provider assignment does not guarantee currency or merchant-account eligibility. A missing connection, unsupported local currency, stale exchange rate, or unknown residence pauses checkout with payment support. There is no cross-provider fallback or substituted charge currency.

An in-progress checkout pins its existing quote, including after the quote expires or residence changes. A competing checkout is blocked. An open eligible attempt can resume; expired or ambiguous attempts require reconciliation before another payment. Test transactions never grant live access. Historical signed provider callbacks continue to reconcile even when the student's current country would select a different provider.

New manual receipt uploads and legacy reference-only student submissions return HTTP 410. Historical receipts, private receipt downloads, administrator review, ledger entries, and saved payment agreements are retained. Existing tuition amounts and installment deadlines are unchanged.

## Release checks

- Country mapping covers all 60 UN M49 African country/territory codes, plus representative non-African and unknown residences.
- Browser form-action policy allows the exact validated Stripe and Flutterwave checkout hosts, retaining the existing script and frame protections.
- Both the local checkout endpoint and legacy invoice checkout endpoints enforce routing on the server. Posted provider/country/amount fields cannot override the saved quote.
- Ownership, CSRF, email/MFA requirements, verified settlement, exact tender matching, idempotency, and existing access restrictions remain in place.
- Regression tests seed pre-migration receipts directly to verify their continuing private download and administrator reconciliation; no test-only switch reopens production manual submissions.
- Use mocked provider transports in local tests. No live charge, enrollment, or customer notification is created for release testing.
- Browser preview could not start in this environment (`Storage.setCookies: Invalid cookie fields`); editable Figma compositions were visually checked. Production signed-in checkout and a real provider payment still need an account-level smoke test.

References: UN M49 https://unstats.un.org/unsd/methodology/m49/overview/ ; Flutterwave supported currencies https://flutterwave.com/us/support/general/what-are-the-currencies-accepted-on-flutterwave ; existing server-side provider verification implementations in `local_payments.py`, `global_portal.py`, and `flutterwave_payments.py`.
