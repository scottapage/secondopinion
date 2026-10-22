# SecondOpinion - notes for future sessions

- Proof of concept for 2degrees: NZ business mobile/broadband bill analysis. Live at www.secondopinion.co.nz
  (DigitalOcean syd1, auto-deploys from `main` within a minute). Secrets live only on the server.
- V2 lives on `main`; branch `v1` is the frozen first release. The server deploys the branch named in
  /var/lib/secondopinion/deploy-branch (Admin > Settings > Site version). Keep DB migrations additive so V1 still runs.
- Any NZ provider is accepted (mobile and/or broadband; energy bills with broadband keep power/gas as `non_telco`).
- Offers shown to customers are only what the catalogue/benefits agents found published on 2degrees.nz.
- Front-page meter and share cards are anonymised: amounts and provider names only, never names, numbers or companies.
- Porting a number needs only MSISDN + losing provider + LSP account number + LSP account name - all on the bill.
  Missing: the user's real name and a delivery address (or eSIM). See docs/PORTING.md.
- Customers often have services across several providers; keep every line/service tagged with its provider.
- Never execute model-generated code. Parsing recipes are data applied by app/specparse.py.
- Money is NZD excl GST throughout.
