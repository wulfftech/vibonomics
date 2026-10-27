## What and why

<!-- One paragraph on the problem, one on the approach. Link the issue if there is one. -->

## Checks

- [ ] `cd backend && ruff check vibonomics tests && ruff format --check vibonomics tests && pytest -q`
- [ ] `cd frontend && npm run typecheck && npm run lint && npm test && npm run build`
- [ ] `npm run gen:api` run and `src/api/schema.d.ts` committed, if a router or schema changed
- [ ] migration added, if `orm.py` changed
- [ ] tests added or updated for the change
- [ ] no API keys, tokens, hostnames or personal paths in the diff
- [ ] README, `.env.example` or settings text updated where behaviour or configuration changed

## Notes for the reviewer

<!-- Anything that needs a decision, a licence check, or a live provider to verify. -->
