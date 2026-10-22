# SecondOpinion - notes for future sessions

- Proof of concept for 2degrees: NZ business mobile/broadband bill analysis. Live at www.secondopinion.co.nz
  (DigitalOcean syd1, auto-deploys from `main` within a minute). Secrets live only on the server.
- Porting a number needs only MSISDN + losing provider + LSP account number + LSP account name - all on the bill.
  Missing: the user's real name and a delivery address (or eSIM). See docs/PORTING.md.
- Customers often have services across several providers; keep every line/service tagged with its provider.
- Never execute model-generated code. Parsing recipes are data applied by app/specparse.py.
- Money is NZD excl GST throughout.
