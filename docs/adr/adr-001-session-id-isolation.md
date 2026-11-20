# ADR-001: SQLite Session ID Isolation (BOLA Prevention)

## Status
Proposed

## Context
The Mate application needs to prevent cross-session data access (BOLA - Broken Object Level Authorization). All database operations must be filtered by `session_id` to ensure users can only access their own data.

## Decision
Implement session ID filtering at the database query level in `src/mate_api/database.py`. Every SELECT, UPDATE, DELETE operation on tables `sessions`, `tasks`, `habits`, `calendar_blocks`, and `memories` must include a `WHERE session_id = ?` clause.

The `session_id` is set via cookie `mate_sid` with `SameSite=Lax` on login/register.

## Consequences
- **Positive**: Prevents BOLA attacks by design; all queries automatically isolate by session.
- **Negative**: Requires explicit opt-out (rare); developers must remember to include session_id filter; adds a clause to every query.

## Alternatives Considered
- Application-level only: Would leave gaps if new queries are added without filtering.
- JWT validation per request: Overkill for SQLite single-process deployment; session cookie approach is simpler.

---
*This is the first ADR in the project, establishing the security foundation for session isolation.*