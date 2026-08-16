# Architecture

CampusFlow enforces a strict boundary: natural-language interpretation can propose an intent, but only deterministic services decide and execute.

| Module | Purpose | Must not do |
| --- | --- | --- |
| `agents/interpreter.py` | Extract a proposed intent and entities | Authorize actions |
| `policies/guardian.py` | Validate policy identity, relevance and conflicts | Execute tools |
| `permissions/rbac.py` | Check explicit role permissions | Trust frontend roles in production |
| `risk/engine.py` | Deterministically classify risk | Delegate final risk to an LLM |
| `autonomy/engine.py` | Select ACT / ASK / APPROVE / STOP | Bypass policy or permission checks |
| `services/request_service.py` | Orchestrate the guarded workflow | Embed policy decisions in UI |
| `services/audit_service.py` | Persist a traceable event | Make authorization decisions |

## Production seams

The MVP repository stores data in memory so reviewers can run it instantly. Replace the repositories with PostgreSQL/Supabase implementations, authenticate the user server-side, and replace `agents/interpreter.py` with a Gemini/LangGraph proposal node. Keep the existing policy, RBAC, risk and autonomy calls between the model and every tool.

Policy retrieval belongs in `rag/`; its output must include policy ID, version, source section and confidence before the Policy Guardian permits a policy-sensitive request.
