# ADR 0001: Tenant boundaries and core objects

Status: Accepted for M0 (NERE-8).

## Context

Profiles must remain attributable to evidence and isolated by organization.
Employees and login accounts represent different things. A login user joins an
organization through a membership, optionally linked to an employee; explicit
role grants authorize organization administration or department management.

## Decision

- Organization is the tenant. Every business row carries an indexed, non-null
  `org_id` referencing it. Audit events may omit it for platform actions.
- Referenced business tables expose `UNIQUE (org_id, id)`; references use
  composite foreign keys. This includes optional and self references. Queries
  and worker jobs must still check tenant context: foreign keys prevent invalid
  references, not unauthorized reads. M0 does not enable PostgreSQL RLS.
- New record IDs use Python 3.14 `uuid.uuid7`. Times use UTC and PostgreSQL
  `timestamptz`. Enumerations use `StrEnum`, VARCHAR and CHECK constraints, so
  schema changes avoid PostgreSQL native ENUM lifecycle operations. Structured
  fields use JSONB. Composite references have no ORM relationships.
- Evidence content and attribution are immutable after insertion. Only `status`
  and `status_reason` may change, and each change must append an audit event in
  the same transaction. Corrections append new evidence pointing to the original
  via `supersedes_evidence_id`. Audit events are append-only. Mutation APIs and
  enforcement of these write protocols belong to M1; M0 exposes only append
  services and deliberately adds no update/delete interface or database trigger.
- Evidence hashes use UTF-8 JSON with sorted keys (including nested objects),
  compact separators, literal Unicode and no non-finite numbers. The unique
  organization/source/reference/hash key makes appends idempotent; PostgreSQL
  `ON CONFLICT DO NOTHING` also handles concurrent submissions. Both services
  leave transaction commit/rollback to their caller.
- Claim assessment (`confirmed/tentative/unknown/disputed`) is independent from
  review (`candidate/pending_employee/pending_manager/accepted/rejected`). Missing
  evidence never implies inability. Confidence labels are not probabilities.
- Capability definitions belong to versioned dictionaries; claims reference a
  specific definition. An organization has at most one active dictionary.
- Profile versions freeze claim fields and evidence references (ID, kind, title,
  relation), never evidence content. Authorization must still gate evidence reads.
- Pending D3: `level` is nullable text, allowing descriptive rubric labels without
  committing to a numeric grading system. M2 can revisit it. No dependencies added.

## Consequences

Tenant integrity is checked even for direct SQL writes. Explicit scoped queries
replace composite ORM relationships. Changes to dictionary definitions require a
new version; changes to evidence require correction records and audit history.
