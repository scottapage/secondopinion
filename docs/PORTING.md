# Porting a number to 2degrees - what the bill already tells us

To port (transfer) a mobile number from another NZ provider, the gaining provider needs:

| Field | Where it comes from |
|---|---|
| MSISDN (the phone number) | On the bill - `lines.number` |
| Losing service provider (LSP) name - Spark, One NZ, 2degrees | Detected from the bill - `invoices.carrier` |
| LSP account number | On the bill - `invoices.account_number` |
| LSP account name (exactly as the losing provider has it) | On the bill - `invoices.account_name` |

So a port could be initiated by API from the data we hold, without asking the customer for any of it.

What a bill usually does **not** give us:

- the actual **user's name** for each number (bills often show the account holder on every line, e.g. "Jonathan Winstone" × 7), and
- a **delivery address** for a physical SIM.

Both are collected at the point of sale. An **eSIM** removes the address requirement entirely: the plan is to offer
test eSIMs from the UI with a guided setup.

The sales dossier (`app/dossier.py`) lays this out per number, with a readiness tick for each field.
