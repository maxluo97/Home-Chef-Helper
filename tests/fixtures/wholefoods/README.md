# Whole Foods order fixtures

- `synthetic-*.txt` — made-up orders in the exact format of a real Whole Foods
  (Amazon "Your Orders") paste. Product IDs (`B0SYN…`) and items are fictional.
  These are committed and drive the parser's golden tests.
  - `synthetic-01-full.txt`: full paste with the `Items in your order` header and
    `Collapse` footer; covers each/lb pricing, tare weight, promotions, qty > 1,
    prepared foods and pastry (both discarded), and a "1 Each" item sold by lb.
  - `synthetic-02-trimmed.txt`: user-trimmed paste (no header/footer); repeats
    product IDs from 01 so alias reuse by ID can be tested.
- `private/` — real pasted orders. **Git-ignored**; never commit these (they are
  personal purchase history). Tests that read them must skip if the folder is empty.
